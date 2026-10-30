"""프로젝트 안의 폴더와 사진을 큰 아이콘, 작은 아이콘, 자세히 보기로 보여주는 화면."""

from enum import IntEnum
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.folder_contents import (
    EntryDetails,
    format_modified,
    format_size,
    list_image_files,
    name_sort_key,
    read_entry_details,
)
from core.project_folders import add_missing_subfolders, list_project_folders
from ui.folder_watcher import DebouncedFolderWatcher
from ui.message_boxes import confirm, warn
from ui.thumbnail_loader import ThumbnailLoader
from ui.toast import show_toast

ICON_SIZE = QSize(96, 96)
GRID_SIZE = QSize(140, 140)
SMALL_ICON_SIZE = QSize(48, 48)
SMALL_GRID_SIZE = QSize(96, 88)
DETAIL_ICON_SIZE = QSize(20, 20)
DETAIL_HEADERS = ("이름", "수정일", "크기", "종류")
NAME_COLUMN = 0
PATH_SEPARATOR = " / "
ADD_SUBFOLDERS_TITLE = "하위 폴더 추가"
# 항목이 폴더인지 표시하는 데이터 위치. 사진은 눌러도 들어가지 않는다.
IS_FOLDER_ROLE = Qt.ItemDataRole.UserRole


class ViewMode(IntEnum):
    """폴더 내용을 보여주는 방식."""

    LARGE_ICONS = 0
    SMALL_ICONS = 1
    DETAILS = 2


VIEW_MODE_LABELS = {
    ViewMode.LARGE_ICONS: "큰 아이콘",
    ViewMode.SMALL_ICONS: "작은 아이콘",
    ViewMode.DETAILS: "자세히",
}


class _ElidedLabel(QLabel):
    """공간이 부족하면 가운데를 말줄임표로 줄여 표시하는 문구. text()는 전체를 반환한다."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """다른 위젯을 밀어내지 않도록 가로 크기를 공간에 맞춘다."""
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def minimumSizeHint(self) -> QSize:
        """문구가 길어도 최소 너비를 차지하지 않게 한다."""
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, _event: QPaintEvent) -> None:
        """현재 너비에 맞게 줄인 문구를 그린다."""
        elided_text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, self.width()
        )
        painter = QPainter(self)
        painter.drawText(self.rect(), self.alignment(), elided_text)


class _DetailItem(QTreeWidgetItem):
    """자세히 보기의 한 줄. 열마다 표시 문구가 아닌 실제 값으로 정렬한다."""

    def __init__(
        self, name: str, is_folder: bool, details: EntryDetails, icon: QIcon
    ) -> None:
        """표시 문구와 정렬 기준을 설정한다."""
        super().__init__(
            [
                name,
                format_modified(details.modified),
                format_size(details.size),
                details.kind,
            ]
        )
        self.setIcon(0, icon)
        self.setData(0, IS_FOLDER_ROLE, is_folder)
        modified = details.modified.timestamp() if details.modified else -1.0
        size = -1 if details.size is None else details.size
        self._sort_keys = (name_sort_key(name), modified, size, details.kind)

    def __lt__(self, other: "_DetailItem") -> bool:
        """현재 정렬 중인 열의 실제 값으로 비교한다."""
        column = self.treeWidget().sortColumn()
        return self._sort_keys[column] < other._sort_keys[column]


def _build_detail_tree() -> QTreeWidget:
    """자세히 보기 표를 만든다. 제목을 누르기 전까지는 이름순으로 둔다."""
    tree = QTreeWidget()
    tree.setHeaderLabels(DETAIL_HEADERS)
    tree.setRootIsDecorated(False)
    tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
    tree.setIconSize(DETAIL_ICON_SIZE)
    tree.setUniformRowHeights(True)
    tree.setAlternatingRowColors(True)

    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in range(1, len(DETAIL_HEADERS)):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    header.setSortIndicator(NAME_COLUMN, Qt.SortOrder.AscendingOrder)
    tree.setSortingEnabled(True)
    return tree


class FolderBrowser(QWidget):
    """뒤로 가기 버튼, 현재 경로, 보기 선택, 폴더와 사진 목록으로 이루어진 화면."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """위젯을 배치한다. 프로젝트는 set_project()로 지정한다."""
        super().__init__(parent)
        self._project: Path | None = None
        self._current: Path | None = None
        self._shown_entries: tuple[Path, list[str], list[str]] | None = None
        self._image_items: dict[str, QListWidgetItem] = {}
        self._detail_items: dict[str, _DetailItem] = {}
        self._view_mode = ViewMode.LARGE_ICONS
        self._thumbnail_loaders: set[ThumbnailLoader] = set()
        icon_provider = QFileIconProvider()
        self._folder_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)
        self._file_icon = icon_provider.icon(QFileIconProvider.IconType.File)
        QApplication.instance().aboutToQuit.connect(self.stop_thumbnail_loading)

        self._back_button = QPushButton("← 뒤로")
        self._back_button.clicked.connect(self._on_back_clicked)
        self._path_label = _ElidedLabel()
        self._add_subfolders_button = QPushButton("이 위치에 하위 폴더 추가")
        self._add_subfolders_button.clicked.connect(self._on_add_subfolders_clicked)

        self._folder_list = QListWidget()
        self._folder_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._folder_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._folder_list.setMovement(QListWidget.Movement.Static)
        self._folder_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._folder_list.setIconSize(ICON_SIZE)
        self._folder_list.setGridSize(GRID_SIZE)
        self._folder_list.setWordWrap(True)
        self._folder_list.itemClicked.connect(self._on_item_clicked)

        self._detail_tree = _build_detail_tree()
        self._detail_tree.itemClicked.connect(self._on_detail_item_clicked)

        self._empty_label = QLabel("폴더나 사진이 없습니다")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self._back_button)
        header_layout.addWidget(self._path_label, stretch=1)
        self._view_buttons = QButtonGroup(self)
        for mode, label in VIEW_MODE_LABELS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            self._view_buttons.addButton(button, mode)
            header_layout.addWidget(button)
        self._view_buttons.button(self._view_mode).setChecked(True)
        self._view_buttons.idClicked.connect(self._on_view_button_clicked)
        header_layout.addWidget(self._add_subfolders_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_layout)
        layout.addWidget(self._folder_list, stretch=1)
        layout.addWidget(self._detail_tree, stretch=1)
        layout.addWidget(self._empty_label, stretch=1)

        self._watcher = DebouncedFolderWatcher(self)
        self._watcher.changed.connect(self._refresh)

        self._show_no_folder()

    def set_project(self, project: Path | None) -> None:
        """표시할 프로젝트를 바꾸고, 그 프로젝트의 맨 위를 보여준다."""
        self._project = project
        if project is None or not self._navigate_to(project):
            self._show_no_folder()

    def set_view_mode(self, mode: ViewMode) -> None:
        """보기 방식을 바꾼다. 폴더를 옮겨도 선택한 보기는 유지된다."""
        self._view_mode = mode
        self._view_buttons.button(mode).setChecked(True)
        if mode == ViewMode.LARGE_ICONS:
            self._folder_list.setIconSize(ICON_SIZE)
            self._folder_list.setGridSize(GRID_SIZE)
        elif mode == ViewMode.SMALL_ICONS:
            self._folder_list.setIconSize(SMALL_ICON_SIZE)
            self._folder_list.setGridSize(SMALL_GRID_SIZE)
        self._update_content_visibility()

    def _on_view_button_clicked(self, mode_id: int) -> None:
        """누른 보기 버튼에 맞게 보기 방식을 바꾼다."""
        self.set_view_mode(ViewMode(mode_id))

    def _update_content_visibility(self) -> None:
        """현재 보기에 맞는 화면을 표시한다. 항목이 없으면 안내 문구를 보여준다."""
        has_entries = self._folder_list.count() > 0
        is_details = self._view_mode == ViewMode.DETAILS
        self._folder_list.setVisible(has_entries and not is_details)
        self._detail_tree.setVisible(has_entries and is_details)
        self._empty_label.setVisible(not has_entries)

    def _navigate_to(self, folder: Path) -> bool:
        """폴더로 이동하고 성공 여부를 반환한다. 읽지 못하면 경고하고 화면을 유지한다."""
        try:
            folder_names = list_project_folders(folder)
            image_names = list_image_files(folder)
        except OSError as error:
            # 프로그램 시작 시에도 메인 창 가운데에 뜨도록, 메인 창이 표시된 뒤로 미룬다.
            QTimer.singleShot(
                0,
                partial(
                    warn,
                    self,
                    "폴더 열기 오류",
                    f"폴더를 열지 못했습니다.\n{folder}\n\n{error}",
                ),
            )
            return False

        # 다른 폴더를 열면 정렬을 이름순으로 되돌리고, 같은 폴더의 새로 고침은 그대로 둔다.
        if folder != self._current:
            self._detail_tree.header().setSortIndicator(
                NAME_COLUMN, Qt.SortOrder.AscendingOrder
            )
        self._current = folder
        # 목록이 그대로이면 다시 그리지 않아 깜빡임과 썸네일 재생성을 막는다.
        entries = (folder, folder_names, image_names)
        if entries != self._shown_entries:
            self._shown_entries = entries
            self._show_entries(folder_names, image_names)
            self._start_thumbnail_loading(folder, image_names)
        self._update_header()
        self._watcher.set_paths(self._watch_targets())
        return True

    def _show_entries(self, folder_names: list[str], image_names: list[str]) -> None:
        """폴더와 사진을 섞어 이름순으로 아이콘 목록과 자세히 표에 함께 채운다."""
        self._stop_current_loaders()
        self._folder_list.clear()
        self._detail_tree.clear()
        self._image_items = {}
        self._detail_items = {}
        # 채우는 동안 항목마다 다시 정렬하지 않도록 정렬을 잠시 끈다.
        self._detail_tree.setSortingEnabled(False)
        for name in sorted([*folder_names, *image_names], key=name_sort_key):
            if name in image_names:
                self._image_items[name] = self._add_entry(name, False, self._file_icon)
            else:
                self._add_entry(name, True, self._folder_icon)
        self._detail_tree.setSortingEnabled(True)
        self._update_content_visibility()

    def _add_entry(self, name: str, is_folder: bool, icon: QIcon) -> QListWidgetItem:
        """항목 하나를 아이콘 목록과 자세히 표에 추가하고, 아이콘 항목을 반환한다."""
        item = QListWidgetItem(icon, name)
        item.setData(IS_FOLDER_ROLE, is_folder)
        self._folder_list.addItem(item)

        details = read_entry_details(self._current / name, is_folder)
        detail_item = _DetailItem(name, is_folder, details, icon)
        self._detail_tree.addTopLevelItem(detail_item)
        self._detail_items[name] = detail_item
        return item

    def _start_thumbnail_loading(self, folder: Path, image_names: list[str]) -> None:
        """사진이 있으면 별도 스레드에서 썸네일 생성을 시작한다."""
        if not image_names:
            return
        loader = ThumbnailLoader(folder, image_names, self)
        loader.thumbnail_ready.connect(self._on_thumbnail_ready)
        loader.finished.connect(self._remove_finished_loaders)
        self._thumbnail_loaders.add(loader)
        loader.start()

    def _stop_current_loaders(self) -> None:
        """진행 중인 썸네일 생성에 중단을 요청한다. 화면을 막지 않도록 기다리지 않는다."""
        for loader in self._thumbnail_loaders:
            loader.requestInterruption()

    def stop_thumbnail_loading(self) -> None:
        """썸네일 생성을 중단하고 스레드가 끝나기를 기다린다. 프로그램 종료 시 호출한다."""
        self._stop_current_loaders()
        for loader in self._thumbnail_loaders:
            loader.wait()

    def _remove_finished_loaders(self) -> None:
        """끝난 썸네일 스레드를 정리한다."""
        for loader in [item for item in self._thumbnail_loaders if item.isFinished()]:
            self._thumbnail_loaders.discard(loader)
            loader.deleteLater()

    def _on_thumbnail_ready(self, folder: str, name: str, image: QImage) -> None:
        """받은 썸네일을 아이콘에 적용한다. 이전 폴더의 결과는 버린다."""
        item = self._image_items.get(name)
        if item is not None and self._current is not None and str(self._current) == folder:
            icon = QIcon(QPixmap.fromImage(image))
            item.setIcon(icon)
            self._detail_items[name].setIcon(0, icon)

    def _show_no_folder(self) -> None:
        """표시할 폴더가 없는 상태로 화면을 바꾸고 감시를 멈춘다."""
        self._current = None
        self._shown_entries = None
        self._show_entries([], [])
        self._update_header()
        self._watcher.set_paths([])

    def _update_header(self) -> None:
        """현재 경로와 버튼 상태를 갱신한다. 하위 폴더 추가는 프로젝트 맨 위에서만 켠다."""
        if self._current is None or self._project is None:
            self._path_label.clear()
            self._back_button.setEnabled(False)
            self._add_subfolders_button.setEnabled(False)
            return
        relative_parts = self._current.relative_to(self._project).parts
        self._path_label.setText(
            PATH_SEPARATOR.join((self._project.name, *relative_parts))
        )
        is_project_top = self._current == self._project
        self._back_button.setEnabled(not is_project_top)
        self._add_subfolders_button.setEnabled(is_project_top)

    def _watch_targets(self) -> list[Path]:
        """프로젝트부터 현재 폴더까지의 경로를 반환한다. 중간 폴더가 사라져도 알기 위해서다."""
        targets = [self._current]
        while targets[-1] != self._project:
            targets.append(targets[-1].parent)
        return targets

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """누른 항목이 폴더이면 들어간다. 사진은 눌러도 아무 일도 하지 않는다."""
        if item.data(IS_FOLDER_ROLE):
            self._navigate_to(self._current / item.text())

    def _on_detail_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """자세히 보기에서 누른 줄이 폴더이면 들어간다."""
        if item.data(0, IS_FOLDER_ROLE):
            self._navigate_to(self._current / item.text(0))

    def _on_back_clicked(self) -> None:
        """한 단계 위 폴더로 올라간다."""
        self._navigate_to(self._current.parent)

    def _on_add_subfolders_clicked(self) -> None:
        """추가 여부를 묻고, 프로젝트에 없는 하위 폴더를 만든 뒤 결과를 알린다."""
        if not confirm(self, ADD_SUBFOLDERS_TITLE, "하위 폴더를 추가하시겠습니까?"):
            return

        try:
            created_count = add_missing_subfolders(self._project)
        except OSError as error:
            warn(
                self,
                ADD_SUBFOLDERS_TITLE,
                f"하위 폴더를 추가하지 못했습니다.\n\n{error}",
            )
            self._refresh()
            return

        self._refresh()
        if created_count:
            show_toast(self, "하위 폴더가 추가되었습니다")
        else:
            show_toast(self, "이미 모든 하위 폴더가 있습니다")

    def _refresh(self) -> None:
        """현재 폴더를 다시 읽는다. 폴더가 사라졌으면 남아 있는 가장 가까운 상위 폴더로 간다."""
        if self._current is None:
            return
        folder = self._current
        while not folder.is_dir() and folder != self._project:
            folder = folder.parent
        if not folder.is_dir():
            self._show_no_folder()
            return
        self._navigate_to(folder)
