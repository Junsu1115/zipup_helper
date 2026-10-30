"""프로젝트 안의 폴더와 사진을 큰 아이콘, 작은 아이콘, 자세히 보기로 보여주는 화면."""

from collections.abc import Callable
from enum import IntEnum
from functools import partial
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, Qt, QTimer, Signal
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
    QMenu,
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
    list_media_files,
    name_sort_key,
    read_entry_details,
)
from core.photo_copy import (
    PhotoCopySummary,
    delete_from_pub,
    is_inside_pub,
    pub_destination,
)
from core.project_folders import (
    SUBFOLDER_TREE,
    add_missing_subfolders,
    has_missing_subfolders,
    list_project_folders,
)
from ui.file_drag import DRAG_PIXMAP_SIZE, start_file_drag
from ui.folder_watcher import DebouncedFolderWatcher
from ui.message_boxes import confirm, warn
from ui.photo_copy_worker import PhotoCopyWorker
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
PUB_MENU_TEXT = "Pub 폴더로 이동"
DELETE_MENU_TEXT = "삭제(Pub 폴더에서만 삭제됩니다)"
PHOTO_COPY_TITLE = "파일 복사"
PHOTO_DELETE_TITLE = "파일 삭제"
# 항목이 폴더인지 표시하는 데이터 위치. 사진은 눌러도 들어가지 않는다.
IS_FOLDER_ROLE = Qt.ItemDataRole.UserRole


def _photo_copy_message(summary: PhotoCopySummary) -> str:
    """복사 결과를 하단 알림 문구로 만든다. 알릴 내용이 없으면 빈 문자열을 반환한다."""
    copied = summary.copied_count
    existing = summary.existing_count
    if copied and existing:
        return f"파일 {copied}개를 복사했습니다. {existing}개는 이미 있습니다"
    if copied:
        return f"파일 {copied}개를 Pub 폴더로 복사했습니다"
    if existing:
        return "이미 Pub 폴더에 있습니다"
    return ""


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
        if is_folder:
            self.setFlags(Qt.ItemFlag.ItemIsEnabled)
        modified = details.modified.timestamp() if details.modified else -1.0
        size = -1 if details.size is None else details.size
        self._sort_keys = (name_sort_key(name), modified, size, details.kind)

    def __lt__(self, other: "_DetailItem") -> bool:
        """현재 정렬 중인 열의 실제 값으로 비교한다."""
        column = self.treeWidget().sortColumn()
        return self._sort_keys[column] < other._sort_keys[column]


class _ImageDragList(QListWidget):
    """사진 항목을 프로그램 밖으로 끌어낼 수 있는 아이콘 목록."""

    def __init__(self, image_path: Callable[[str], Path]) -> None:
        """이름으로 사진 경로를 찾는 함수를 기억한다."""
        super().__init__()
        self._image_path = image_path

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:
        """선택한 사진들의 파일 드래그를 시작한다."""
        items = self.selectedItems()
        if items:
            pixmap = items[0].icon().pixmap(DRAG_PIXMAP_SIZE)
            paths = [self._image_path(item.text()) for item in items]
            start_file_drag(self, paths, pixmap)


class _ImageDragTree(QTreeWidget):
    """사진 줄을 프로그램 밖으로 끌어낼 수 있는 자세히 표."""

    def __init__(self, image_path: Callable[[str], Path]) -> None:
        """이름으로 사진 경로를 찾는 함수를 기억한다."""
        super().__init__()
        self._image_path = image_path

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:
        """선택한 사진들의 파일 드래그를 시작한다."""
        items = self.selectedItems()
        if items:
            pixmap = items[0].icon(0).pixmap(DRAG_PIXMAP_SIZE)
            paths = [self._image_path(item.text(0)) for item in items]
            start_file_drag(self, paths, pixmap)


def _build_detail_tree(image_path: Callable[[str], Path]) -> QTreeWidget:
    """자세히 보기 표를 만든다. 제목을 누르기 전까지는 이름순으로 둔다."""
    tree = _ImageDragTree(image_path)
    tree.setDragEnabled(True)
    tree.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)
    tree.setHeaderLabels(DETAIL_HEADERS)
    tree.setRootIsDecorated(False)
    tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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

    # 선택한 사진들이 바뀌면 경로 목록을 보낸다. 선택이 없으면 빈 목록이다.
    images_selected = Signal(object)
    # 사진을 더블클릭하면 그 경로를 보낸다.
    image_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """위젯을 배치한다. 프로젝트는 set_project()로 지정한다."""
        super().__init__(parent)
        self._project: Path | None = None
        self._current: Path | None = None
        self._selected_images: list[Path] = []
        self._copy_worker: PhotoCopyWorker | None = None
        self._last_folder_click_time = 0.0
        self._shown_entries: tuple[Path, list[str], list[str]] | None = None
        self._image_items: dict[str, QListWidgetItem] = {}
        self._detail_items: dict[str, _DetailItem] = {}
        self._view_mode = ViewMode.LARGE_ICONS
        self._thumbnail_loaders: set[ThumbnailLoader] = set()
        icon_provider = QFileIconProvider()
        self._folder_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)
        self._file_icon = icon_provider.icon(QFileIconProvider.IconType.File)
        QApplication.instance().aboutToQuit.connect(self.stop_thumbnail_loading)
        QApplication.instance().aboutToQuit.connect(self.wait_for_photo_copy)

        self._back_button = QPushButton("← 뒤로")
        self._back_button.clicked.connect(self._on_back_clicked)
        self._path_label = _ElidedLabel()
        self._add_subfolders_button = QPushButton("이 위치에 하위 폴더 추가")
        self._add_subfolders_button.clicked.connect(self._on_add_subfolders_clicked)

        self._folder_list = _ImageDragList(self._image_path)
        self._folder_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._folder_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._folder_list.setMovement(QListWidget.Movement.Static)
        # Static은 끌기를 끄므로, 그 뒤에 프로그램 밖으로 끌어내는 것만 다시 켠다.
        self._folder_list.setDragEnabled(True)
        self._folder_list.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self._folder_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._folder_list.setSelectionRectVisible(True)
        self._folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._folder_list.customContextMenuRequested.connect(
            self._on_list_context_menu
        )
        self._folder_list.setIconSize(ICON_SIZE)
        self._folder_list.setGridSize(GRID_SIZE)
        self._folder_list.setWordWrap(True)
        self._folder_list.itemClicked.connect(self._on_item_clicked)
        self._folder_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._folder_list.itemSelectionChanged.connect(self._on_list_selection_changed)

        self._detail_tree = _build_detail_tree(self._image_path)
        self._detail_tree.itemClicked.connect(self._on_detail_item_clicked)
        self._detail_tree.itemDoubleClicked.connect(
            self._on_detail_item_double_clicked
        )
        self._detail_tree.itemSelectionChanged.connect(
            self._on_tree_selection_changed
        )
        self._detail_tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )

        self._empty_label = QLabel("폴더나 파일이 없습니다")
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
            image_names = list_media_files(folder)
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
        # 다시 채우는 동안 생기는 선택 변화를 사용자의 선택으로 처리하지 않는다.
        with QSignalBlocker(self._folder_list), QSignalBlocker(self._detail_tree):
            self._folder_list.clear()
            self._detail_tree.clear()
            self._image_items = {}
            self._detail_items = {}
            # 채우는 동안 항목마다 다시 정렬하지 않도록 정렬을 잠시 끈다.
            self._detail_tree.setSortingEnabled(False)
            for name in sorted([*folder_names, *image_names], key=name_sort_key):
                if name in image_names:
                    self._image_items[name] = self._add_entry(
                        name, False, self._file_icon
                    )
                else:
                    self._add_entry(name, True, self._folder_icon)
            self._detail_tree.setSortingEnabled(True)
        self._restore_selection(image_names)
        self._update_content_visibility()

    def _image_path(self, name: str) -> Path:
        """현재 폴더에 있는 사진의 경로를 반환한다."""
        return self._current / name

    def _restore_selection(self, image_names: list[str]) -> None:
        """같은 폴더에 남아 있는 사진만 다시 선택한다."""
        names = [
            path.name
            for path in self._selected_images
            if path.parent == self._current and path.name in image_names
        ]
        self._select_images(names)

    def _select_images(
        self, names: list[str], update_list: bool = True, update_tree: bool = True
    ) -> None:
        """사진들을 선택하고 화면 표시를 맞춘다. 바뀌었을 때만 신호를 보낸다."""
        with QSignalBlocker(self._folder_list), QSignalBlocker(self._detail_tree):
            if update_list:
                self._folder_list.clearSelection()
                for name in names:
                    self._image_items[name].setSelected(True)
            if update_tree:
                self._detail_tree.clearSelection()
                for name in names:
                    self._detail_items[name].setSelected(True)

        paths = [
            self._current / name for name in sorted(names, key=name_sort_key)
        ]
        if paths != self._selected_images:
            self._selected_images = paths
            self.images_selected.emit(paths)

    def _on_list_selection_changed(self) -> None:
        """아이콘 목록의 선택을 반영한다. 끌어서 선택하는 중이므로 목록 자체는 건드리지 않는다."""
        names = [item.text() for item in self._folder_list.selectedItems()]
        self._select_images(names, update_list=False)

    def _on_tree_selection_changed(self) -> None:
        """자세히 표의 선택을 반영한다. 끌어서 선택하는 중이므로 표 자체는 건드리지 않는다."""
        names = [item.text(0) for item in self._detail_tree.selectedItems()]
        self._select_images(names, update_tree=False)

    def _on_list_context_menu(self, position: QPoint) -> None:
        """아이콘 목록에서 오른쪽 버튼을 누른 사진의 메뉴를 띄운다."""
        item = self._folder_list.itemAt(position)
        if item is not None and not item.data(IS_FOLDER_ROLE):
            global_position = self._folder_list.viewport().mapToGlobal(position)
            self._show_image_menu(item.text(), global_position)

    def _on_tree_context_menu(self, position: QPoint) -> None:
        """자세히 표에서 오른쪽 버튼을 누른 사진의 메뉴를 띄운다."""
        item = self._detail_tree.itemAt(position)
        if item is not None and not item.data(0, IS_FOLDER_ROLE):
            global_position = self._detail_tree.viewport().mapToGlobal(position)
            self._show_image_menu(item.text(0), global_position)

    def _show_image_menu(self, name: str, global_position: QPoint) -> None:
        """위치에 맞는 메뉴를 띄운다. 선택되지 않은 파일을 눌렀으면 그 파일만 선택한다.

        Pub 안에서는 삭제, Work 안에서는 Pub으로 복사 메뉴를 띄우고, 그 밖에서는 띄우지 않는다.
        """
        if is_inside_pub(self._current, self._project):
            menu_text, handler = DELETE_MENU_TEXT, self._delete_selected_from_pub
        elif pub_destination(self._current, self._project) is not None:
            menu_text, handler = PUB_MENU_TEXT, self._start_photo_copy
        else:
            return
        if self._current / name not in self._selected_images:
            self._select_images([name])

        menu = QMenu(self)
        action = menu.addAction(menu_text)
        chosen_action = menu.exec(global_position)
        menu.deleteLater()
        if chosen_action == action:
            handler()

    def _delete_selected_from_pub(self) -> None:
        """확인을 받은 뒤 선택한 파일을 휴지통으로 옮기고 결과를 알린다."""
        selected = list(self._selected_images)
        question = (
            f"선택한 파일 {len(selected)}개를 삭제하시겠습니까?\n"
            "(Pub 폴더에서만 삭제됩니다)"
        )
        if not confirm(self, PHOTO_DELETE_TITLE, question):
            return

        summary = delete_from_pub(selected, self._project)
        self._refresh()
        if summary.deleted_count:
            show_toast(self, f"파일 {summary.deleted_count}개를 삭제했습니다")
        if summary.failures:
            warn(self, PHOTO_DELETE_TITLE, "\n".join(summary.failures))

    def _start_photo_copy(self) -> None:
        """선택한 사진을 별도 스레드에서 Pub 폴더로 복사한다."""
        if self._copy_worker is not None:
            show_toast(self, "복사 중입니다")
            return
        worker = PhotoCopyWorker(list(self._selected_images), self._project, self)
        worker.copy_finished.connect(self._on_photo_copy_finished)
        worker.finished.connect(self._on_copy_worker_stopped)
        self._copy_worker = worker
        worker.start()

    def _on_copy_worker_stopped(self) -> None:
        """끝난 복사 스레드를 정리한다."""
        self._copy_worker.deleteLater()
        self._copy_worker = None

    def wait_for_photo_copy(self) -> None:
        """진행 중인 사진 복사가 끝나기를 기다린다. 프로그램 종료 시 호출한다."""
        if self._copy_worker is not None:
            self._copy_worker.wait()

    def _on_photo_copy_finished(self, summary: PhotoCopySummary) -> None:
        """복사 결과를 하단 알림으로 보여주고, 실패한 파일은 경고창으로 알린다."""
        message = _photo_copy_message(summary)
        if message:
            show_toast(self, message)
        if summary.failures:
            warn(self, PHOTO_COPY_TITLE, "\n".join(summary.failures))

    def _add_entry(self, name: str, is_folder: bool, icon: QIcon) -> QListWidgetItem:
        """항목 하나를 아이콘 목록과 자세히 표에 추가하고, 아이콘 항목을 반환한다."""
        item = QListWidgetItem(icon, name)
        item.setData(IS_FOLDER_ROLE, is_folder)
        # 폴더는 누르면 들어가므로 선택 표시를 하지 않는다.
        if is_folder:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
        """현재 경로와 버튼 상태를 갱신한다.

        하위 폴더 추가는 프로젝트 맨 위에서, 빠진 하위 폴더가 있을 때만 켠다.
        """
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
        self._add_subfolders_button.setEnabled(
            is_project_top and has_missing_subfolders(self._project)
        )

    def _watch_targets(self) -> list[Path]:
        """감시할 폴더를 반환한다.

        프로젝트부터 현재 폴더까지는 중간 폴더가 사라져도 알기 위해 감시한다.
        프로젝트 맨 위에서는 하위 폴더 추가 버튼을 갱신하기 위해 하위 폴더도 감시한다.
        """
        targets = [self._current]
        while targets[-1] != self._project:
            targets.append(targets[-1].parent)
        if self._current == self._project:
            subfolders = (self._project / name for name in SUBFOLDER_TREE)
            targets.extend(folder for folder in subfolders if folder.is_dir())
        return targets

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """누른 항목이 폴더이면 들어간다. 사진은 선택만 된다."""
        if item.data(IS_FOLDER_ROLE):
            self._enter_clicked_folder(item.text())

    def _on_detail_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """자세히 보기에서 누른 줄이 폴더이면 들어간다."""
        if item.data(0, IS_FOLDER_ROLE):
            self._enter_clicked_folder(item.text(0))

    def _enter_clicked_folder(self, name: str) -> None:
        """누른 폴더로 들어가고, 더블클릭 구분을 위해 시각을 기억한다."""
        self._last_folder_click_time = monotonic()
        self._navigate_to(self._current / name)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """아이콘 목록에서 더블클릭한 항목을 처리한다."""
        if not item.data(IS_FOLDER_ROLE):
            self._activate_image(item.text())

    def _on_detail_item_double_clicked(
        self, item: QTreeWidgetItem, _column: int
    ) -> None:
        """자세히 표에서 더블클릭한 줄을 처리한다."""
        if not item.data(0, IS_FOLDER_ROLE):
            self._activate_image(item.text(0))

    def _activate_image(self, name: str) -> None:
        """사진을 더블클릭했다고 알린다.

        폴더를 더블클릭하면 첫 클릭에 들어가므로, 두 번째 클릭이 새 폴더의 사진에
        닿을 수 있다. 폴더에 들어간 직후의 더블클릭은 무시한다.
        """
        interval_seconds = QApplication.doubleClickInterval() / 1000
        if monotonic() - self._last_folder_click_time >= interval_seconds:
            self.image_activated.emit(self._current / name)

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
