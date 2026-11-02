"""프로젝트 안의 폴더와 사진을 큰 아이콘, 작은 아이콘, 자세히 보기로 보여주는 화면."""

from collections.abc import Callable
from enum import IntEnum
from functools import partial
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
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

from core.file_move import move_within_target
from core.folder_contents import (
    MEDIA_SUFFIXES,
    EntryDetails,
    format_modified,
    format_size,
    is_media_file,
    list_all_files,
    list_media_files,
    name_sort_key,
    read_entry_details,
)
from core.photo_copy import (
    DROP_TARGET_FOLDERS,
    PUB_FOLDER,
    WORK_FOLDER,
    PhotoCopySummary,
    copy_files_to_drop_target,
    copy_folder_to_pub,
    copy_photos_to_pub,
    delete_from_pub,
    drop_target_containing,
    is_inside_pub,
    pub_destination,
)
from core.project_folders import (
    SUBFOLDER_TREE,
    add_missing_subfolders,
    create_folders,
    has_missing_subfolders,
    list_project_folders,
)
from ui.copy_confirm_dialog import CopyConfirmDialog
from ui.copy_drop import DropRules, FolderDropHandler, MoveDropButton
from ui.file_drag import DRAG_PIXMAP_SIZE, start_file_drag
from ui.folder_watcher import DebouncedFolderWatcher
from ui.message_boxes import confirm, confirm_ok_cancel, warn
from ui.multi_folder_dialog import MultiFolderDialog
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
ADD_FOLDER_MENU_TEXT = "폴더 추가하기"
DELETE_MENU_TEXT = "삭제(Pub 폴더에서만 삭제됩니다)"
PHOTO_COPY_TITLE = "파일 복사"
FILE_MOVE_TITLE = "파일 이동"
# 놓았을 때 실제로 복사하는 폴더. Reference는 아직 확인창까지만 표시한다.
COPY_ENABLED_TARGETS = (WORK_FOLDER,)
INVALID_DROP_MESSAGE = "Work 폴더와 Reference 폴더에만 이동 가능합니다."
PHOTO_DELETE_TITLE = "파일 삭제"
# 항목이 폴더인지 표시하는 데이터 위치. 사진은 눌러도 들어가지 않는다.
IS_FOLDER_ROLE = Qt.ItemDataRole.UserRole


def _is_plain_file(name: str) -> bool:
    """사진이나 동영상이 아닌 파일 이름인지 반환한다."""
    return Path(name).suffix.lower() not in MEDIA_SUFFIXES


def _photo_copy_message(summary: PhotoCopySummary, folder_name: str) -> str:
    """복사 결과를 하단 알림 문구로 만든다. 알릴 내용이 없으면 빈 문자열을 반환한다."""
    copied = summary.copied_count
    existing = summary.existing_count
    if copied and existing:
        return f"파일 {copied}개를 복사했습니다. {existing}개는 이미 있습니다"
    if copied:
        return f"파일 {copied}개를 {folder_name} 폴더로 복사했습니다"
    if existing:
        return f"이미 {folder_name} 폴더에 있습니다"
    if summary.folder_created and not summary.failures:
        return f"폴더를 {folder_name} 폴더로 복사했습니다"
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


class _FolderDragMixin:
    """폴더 끌기를 직접 처리한다. Qt는 선택된 항목만 끌 수 있는데 폴더는 선택되지 않는다."""

    _pressed_folder: str | None = None
    _press_position: QPoint | None = None
    _folder_was_dragged = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """끌 수 있는 폴더를 눌렀는지 기억한다."""
        position = event.position().toPoint()
        self._pressed_folder = self._draggable_folder_at(position)
        self._press_position = position
        self._folder_was_dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """누른 폴더를 일정 거리 이상 끌면 드래그를 시작한다."""
        if self._pressed_folder is None or not (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            super().mouseMoveEvent(event)
            return
        distance = event.position().toPoint() - self._press_position
        if distance.manhattanLength() >= QApplication.startDragDistance():
            name = self._pressed_folder
            self._pressed_folder = None
            self._folder_was_dragged = True
            start_file_drag(self, [self._image_path(name)], self._folder_pixmap(name))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """폴더를 끈 뒤에는 클릭으로 처리해 그 폴더로 들어가지 않게 한다."""
        self._pressed_folder = None
        if self._folder_was_dragged:
            self._folder_was_dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ImageDragList(_FolderDragMixin, QListWidget):
    """사진을 끌어낼 수 있고, 대상 폴더 위에 놓을 수 있는 아이콘 목록."""

    def __init__(
        self, image_path: Callable[[str], Path], drop_rules: DropRules
    ) -> None:
        """이름으로 사진 경로를 찾는 함수와 놓기 처리를 준비한다."""
        super().__init__()
        self._image_path = image_path
        self._drop_handler = FolderDropHandler(
            self._name_at, self._set_highlight, drop_rules
        )

    def _draggable_folder_at(self, position: QPoint) -> str | None:
        """위치의 항목이 끌 수 있는 폴더이면 이름을 반환한다."""
        item = self.itemAt(position)
        if (
            item is not None
            and item.data(IS_FOLDER_ROLE)
            and item.flags() & Qt.ItemFlag.ItemIsDragEnabled
        ):
            return item.text()
        return None

    def _folder_pixmap(self, name: str) -> QPixmap:
        """끄는 동안 표시할 폴더 아이콘을 반환한다."""
        item = self.findItems(name, Qt.MatchFlag.MatchExactly)[0]
        return item.icon().pixmap(DRAG_PIXMAP_SIZE)

    def _name_at(self, event: QDropEvent) -> str | None:
        """이벤트 위치의 항목 이름을 반환한다."""
        item = self.itemAt(event.position().toPoint())
        return None if item is None else item.text()

    def _set_highlight(self, name: str, is_on: bool) -> None:
        """대상 폴더의 강조를 켜거나 끈다."""
        color = self.palette().highlight() if is_on else QBrush()
        for item in self.findItems(name, Qt.MatchFlag.MatchExactly):
            item.setBackground(color)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """프로그램 안에서 끈 파일만 받는다."""
        self._drop_handler.drag_enter(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """대상 폴더 위에서만 놓을 수 있게 한다."""
        self._drop_handler.drag_move(event)

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        """강조를 지운다."""
        self._drop_handler.drag_leave()

    def dropEvent(self, event: QDropEvent) -> None:
        """대상 폴더 위에 놓으면 복사를 요청한다."""
        self._drop_handler.drop(event)

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:
        """선택한 파일들의 드래그를 시작한다."""
        items = self.selectedItems()
        if items:
            pixmap = items[0].icon().pixmap(DRAG_PIXMAP_SIZE)
            paths = [self._image_path(item.text()) for item in items]
            start_file_drag(self, paths, pixmap)


class _ImageDragTree(_FolderDragMixin, QTreeWidget):
    """사진 줄을 끌어낼 수 있고, 대상 폴더 줄 위에 놓을 수 있는 자세히 표."""

    def __init__(
        self, image_path: Callable[[str], Path], drop_rules: DropRules
    ) -> None:
        """이름으로 사진 경로를 찾는 함수와 놓기 처리를 준비한다."""
        super().__init__()
        self._image_path = image_path
        self._drop_handler = FolderDropHandler(
            self._name_at, self._set_highlight, drop_rules
        )

    def _draggable_folder_at(self, position: QPoint) -> str | None:
        """위치의 줄이 끌 수 있는 폴더이면 이름을 반환한다."""
        item = self.itemAt(position)
        if (
            item is not None
            and item.data(0, IS_FOLDER_ROLE)
            and item.flags() & Qt.ItemFlag.ItemIsDragEnabled
        ):
            return item.text(0)
        return None

    def _folder_pixmap(self, name: str) -> QPixmap:
        """끄는 동안 표시할 폴더 아이콘을 반환한다."""
        item = self.findItems(name, Qt.MatchFlag.MatchExactly)[0]
        return item.icon(0).pixmap(DRAG_PIXMAP_SIZE)

    def _name_at(self, event: QDropEvent) -> str | None:
        """이벤트 위치의 줄 이름을 반환한다."""
        item = self.itemAt(event.position().toPoint())
        return None if item is None else item.text(0)

    def _set_highlight(self, name: str, is_on: bool) -> None:
        """대상 폴더 줄의 강조를 켜거나 끈다."""
        color = self.palette().highlight() if is_on else QBrush()
        for item in self.findItems(name, Qt.MatchFlag.MatchExactly):
            for column in range(self.columnCount()):
                item.setBackground(column, color)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """프로그램 안에서 끈 파일만 받는다."""
        self._drop_handler.drag_enter(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """대상 폴더 줄 위에서만 놓을 수 있게 한다."""
        self._drop_handler.drag_move(event)

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        """강조를 지운다."""
        self._drop_handler.drag_leave()

    def dropEvent(self, event: QDropEvent) -> None:
        """대상 폴더 줄 위에 놓으면 복사를 요청한다."""
        self._drop_handler.drop(event)

    def startDrag(self, _supported_actions: Qt.DropAction) -> None:
        """선택한 파일들의 드래그를 시작한다."""
        items = self.selectedItems()
        if items:
            pixmap = items[0].icon(0).pixmap(DRAG_PIXMAP_SIZE)
            paths = [self._image_path(item.text(0)) for item in items]
            start_file_drag(self, paths, pixmap)


def _build_detail_tree(
    image_path: Callable[[str], Path], drop_rules: DropRules
) -> QTreeWidget:
    """자세히 보기 표를 만든다. 제목을 누르기 전까지는 이름순으로 둔다."""
    tree = _ImageDragTree(image_path, drop_rules)
    tree.setDragEnabled(True)
    tree.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
    tree.setDropIndicatorShown(False)
    # 실제 드래그는 표시 영역으로 전달되므로, 표시 영역이 받아야 줄을 찾을 수 있다.
    tree.viewport().setAcceptDrops(True)
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
    # 폴더가 아닌 곳에 놓았거나 사진, 동영상이 없을 때 파일 경로 목록을 보낸다.
    other_files_dropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """위젯을 배치한다. 프로젝트는 set_project()로 지정한다."""
        super().__init__(parent)
        self._project: Path | None = None
        self._current: Path | None = None
        self._selected_images: list[Path] = []
        self._copy_worker: PhotoCopyWorker | None = None
        self._copy_folder_name = PUB_FOLDER
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

        self._back_button = MoveDropButton(
            "← 뒤로", self._parent_move_target, self._on_files_moved
        )
        self._back_button.clicked.connect(self._on_back_clicked)
        self._path_label = _ElidedLabel()
        self._add_subfolders_button = QPushButton("이 위치에 하위 폴더 추가")
        self._add_subfolders_button.clicked.connect(self._on_add_subfolders_clicked)

        drop_rules = DropRules(
            is_folder=self._is_folder_name,
            drop_target=self._drop_target_for,
            on_drop=self.handle_files_dropped,
            on_invalid_drop=self.warn_invalid_drop,
            on_other_drop=self.other_files_dropped.emit,
            area_target=self._drop_area_target,
            move_target=self._move_target_for,
            on_move=self._on_files_moved,
        )
        # 빈 폴더처럼 목록이 숨겨져 있어도 놓을 수 있게 화면 전체가 파일을 받는다.
        self.setAcceptDrops(True)
        self._area_drop_handler = FolderDropHandler(
            lambda _event: None, lambda _name, _is_on: None, drop_rules
        )
        self._folder_list = _ImageDragList(self._image_path, drop_rules)
        self._folder_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._folder_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._folder_list.setMovement(QListWidget.Movement.Static)
        # Static은 끌기를 끄므로, 그 뒤에 끌기와 놓기를 다시 켠다.
        self._folder_list.setDragEnabled(True)
        self._folder_list.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self._folder_list.setDropIndicatorShown(False)
        # 실제 드래그는 표시 영역으로 전달되므로, 표시 영역이 받아야 폴더를 찾을 수 있다.
        self._folder_list.viewport().setAcceptDrops(True)
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

        self._detail_tree = _build_detail_tree(self._image_path, drop_rules)
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
        self._empty_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._empty_label.customContextMenuRequested.connect(
            self._on_empty_label_context_menu
        )

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

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """프로그램 밖에서 끌어온 파일만 받는다."""
        self._area_drop_handler.drag_enter(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """프로그램 밖에서 끌어온 파일만 놓을 수 있게 한다."""
        self._area_drop_handler.drag_move(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """목록 밖에 놓은 파일을 처리한다."""
        self._area_drop_handler.drop(event)

    def _drop_area_target(self) -> str | None:
        """지금 Work나 Reference 안을 보고 있으면 그 폴더 이름을 반환한다."""
        if self._current is None or self._project is None:
            return None
        return drop_target_containing(self._current, self._project)

    def _move_target_for(self, name: str, paths: list[Path]) -> Path | None:
        """Work, Reference 안에서 항목을 옮길 수 있는 폴더 경로를 반환한다."""
        if self._drop_area_target() is None or not self._is_folder_name(name):
            return None
        folder = self._current / name
        return None if folder in paths else folder

    def _parent_move_target(self, paths: list[Path]) -> Path | None:
        """뒤로 버튼에 놓았을 때 옮길 위 폴더를 반환한다. Work, Reference 밖으로는 나갈 수 없다."""
        area_target = self._drop_area_target()
        if area_target is None or self._current == self._project / area_target:
            return None
        return self._current.parent if paths else None

    def _on_files_moved(self, destination: Path, paths: list[Path]) -> None:
        """항목을 옮긴다. 끌기가 끝난 뒤에 화면을 다시 그리도록 미룬다."""
        QTimer.singleShot(0, partial(self._move_files, destination, paths))

    def _move_files(self, destination: Path, paths: list[Path]) -> None:
        """항목을 옮기고 화면을 새로 고친 뒤 결과를 알린다."""
        summary = move_within_target(paths, destination, self._project)
        self._refresh()
        if summary.moved_count and summary.existing_count:
            show_toast(
                self,
                f"{summary.moved_count}개를 옮겼습니다. "
                f"{summary.existing_count}개는 이미 있습니다",
            )
        elif summary.moved_count:
            show_toast(self, f"{summary.moved_count}개를 옮겼습니다")
        elif summary.existing_count:
            show_toast(self, "옮길 폴더에 같은 이름이 이미 있습니다")
        if summary.failures:
            warn(self, FILE_MOVE_TITLE, "\n".join(summary.failures))

    def _is_folder_name(self, name: str) -> bool:
        """지금 화면에 있는 항목이 폴더인지 반환한다."""
        return name in self._detail_items and name not in self._image_items

    def _drop_target_for(self, name: str) -> str | None:
        """프로젝트 맨 위의 Work, Reference 폴더이면 복사 대상 이름을 반환한다."""
        if (
            self._current == self._project
            and name in DROP_TARGET_FOLDERS
            and self._is_folder_name(name)
        ):
            return name
        return None

    def handle_files_dropped(self, target_name: str, paths: list[Path]) -> None:
        """놓은 파일을 처리한다. 끌기가 끝난 뒤에 확인창이 뜨도록 미룬다."""
        QTimer.singleShot(0, partial(self._confirm_file_copy, target_name, paths))

    def warn_invalid_drop(self) -> None:
        """복사할 수 없는 폴더라고 알린다. 끌기가 끝난 뒤에 경고창이 뜨도록 미룬다."""
        QTimer.singleShot(
            0, partial(warn, self, PHOTO_COPY_TITLE, INVALID_DROP_MESSAGE)
        )

    def _confirm_file_copy(self, target_name: str, paths: list[Path]) -> None:
        """썸네일과 함께 복사 여부를 묻고, OK를 누르면 사진과 동영상만 복사한다."""
        if self._copy_worker is not None:
            show_toast(self, "복사 중입니다")
            return
        media_paths = [path for path in paths if is_media_file(path)]
        if self._project is None or not media_paths:
            return
        skipped = [
            f"'{path.name}': 사진이나 동영상이 아니어서 건너뛰었습니다."
            for path in paths
            if path not in media_paths
        ]
        if skipped:
            warn(self, PHOTO_COPY_TITLE, "\n".join(skipped))
        dialog = CopyConfirmDialog(target_name, media_paths, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        dialog.deleteLater()
        if accepted and target_name in COPY_ENABLED_TARGETS:
            self._start_copy(
                partial(
                    copy_files_to_drop_target, media_paths, self._project, target_name
                ),
                target_name,
            )

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

    def _list_files(self, folder: Path) -> list[str]:
        """Work, Reference 안에서는 모든 파일을, 그 밖에서는 사진과 동영상만 반환한다."""
        if self._project is not None and drop_target_containing(folder, self._project):
            return list_all_files(folder)
        return list_media_files(folder)

    def _navigate_to(self, folder: Path) -> bool:
        """폴더로 이동하고 성공 여부를 반환한다. 읽지 못하면 경고하고 화면을 유지한다."""
        try:
            folder_names = list_project_folders(folder)
            image_names = self._list_files(folder)
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
        """아이콘 목록에서 오른쪽 버튼을 누른 곳의 메뉴를 띄운다."""
        item = self._folder_list.itemAt(position)
        global_position = self._folder_list.viewport().mapToGlobal(position)
        if item is None:
            self._show_context_menu(None, None, global_position)
        elif item.data(IS_FOLDER_ROLE):
            self._show_context_menu(None, item.text(), global_position)
        else:
            self._show_context_menu(item.text(), None, global_position)

    def _on_tree_context_menu(self, position: QPoint) -> None:
        """자세히 표에서 오른쪽 버튼을 누른 곳의 메뉴를 띄운다."""
        item = self._detail_tree.itemAt(position)
        global_position = self._detail_tree.viewport().mapToGlobal(position)
        if item is None:
            self._show_context_menu(None, None, global_position)
        elif item.data(0, IS_FOLDER_ROLE):
            self._show_context_menu(None, item.text(0), global_position)
        else:
            self._show_context_menu(item.text(0), None, global_position)

    def _on_empty_label_context_menu(self, position: QPoint) -> None:
        """빈 폴더 화면에서 오른쪽 버튼을 누른 곳의 메뉴를 띄운다."""
        self._show_context_menu(None, None, self._empty_label.mapToGlobal(position))

    def _menu_entries(
        self, file_name: str | None, folder_name: str | None
    ) -> list[tuple[str, Callable[[], None]]]:
        """지금 위치와 누른 항목에 맞는 메뉴 문구와 동작을 반환한다.

        Pub 안의 파일은 삭제, Work 안의 파일과 폴더는 Pub으로 복사할 수 있고,
        Work와 Reference에는 폴더를 추가할 수 있다. 폴더를 눌렀으면 그 안에 추가한다.
        """
        if self._current is None or self._project is None:
            return []
        if is_inside_pub(self._current, self._project):
            if file_name is None:
                return []
            return [(DELETE_MENU_TEXT, self._delete_selected_from_pub)]

        entries: list[tuple[str, Callable[[], None]]] = []
        if pub_destination(self._current, self._project) is not None:
            if file_name is not None:
                entries.append((PUB_MENU_TEXT, self._start_photo_copy))
            elif folder_name is not None:
                entries.append(
                    (
                        PUB_MENU_TEXT,
                        partial(self._start_folder_copy, self._current / folder_name),
                    )
                )
        add_target = self._add_folder_target(folder_name)
        if add_target is not None:
            entries.append(
                (
                    ADD_FOLDER_MENU_TEXT,
                    partial(self._create_folders_from_dialog, add_target),
                )
            )
        return entries

    def _add_folder_target(self, folder_name: str | None) -> Path | None:
        """폴더를 추가할 위치를 반환한다. 폴더를 눌렀으면 그 폴더, 아니면 지금 위치다."""
        target = self._current if folder_name is None else self._current / folder_name
        if drop_target_containing(target, self._project) is None:
            return None
        return target

    def _show_context_menu(
        self, file_name: str | None, folder_name: str | None, global_position: QPoint
    ) -> None:
        """위치에 맞는 메뉴를 띄운다. 선택되지 않은 파일을 눌렀으면 그 파일만 선택한다."""
        entries = self._menu_entries(file_name, folder_name)
        if not entries:
            return
        if file_name is not None and self._current / file_name not in self._selected_images:
            self._select_images([file_name])

        menu = QMenu(self)
        actions = [menu.addAction(text) for text, _handler in entries]
        chosen_action = menu.exec(global_position)
        menu.deleteLater()
        for action, (_text, handler) in zip(actions, entries):
            if chosen_action == action:
                handler()
                return

    def _delete_selected_from_pub(self) -> None:
        """확인을 받은 뒤 선택한 파일을 휴지통으로 옮기고 결과를 알린다."""
        selected = list(self._selected_images)
        question = (
            f"선택한 파일 {len(selected)}개를 삭제하시겠습니까?\n"
            "(Pub 폴더에서만 삭제됩니다)"
        )
        if not confirm(self, PHOTO_DELETE_TITLE, question):
            return

        # Windows에서는 썸네일을 만드느라 읽는 중인 파일을 지울 수 없다.
        self.stop_thumbnail_loading()
        summary = delete_from_pub(selected, self._project)
        self._refresh()
        if summary.deleted_count:
            show_toast(self, f"파일 {summary.deleted_count}개를 삭제했습니다")
        if summary.failures:
            warn(self, PHOTO_DELETE_TITLE, "\n".join(summary.failures))

    def _start_photo_copy(self) -> None:
        """확인을 받은 뒤 선택한 사진을 별도 스레드에서 Pub 폴더로 복사한다."""
        if not self._confirm_pub_copy():
            return
        self._start_copy(
            partial(copy_photos_to_pub, list(self._selected_images), self._project),
            PUB_FOLDER,
        )

    def _confirm_pub_copy(self) -> bool:
        """Pub 복사 여부를 묻는다. 복사 중이면 묻지 않고 알린 뒤 거절한다."""
        if self._copy_worker is not None:
            show_toast(self, "복사 중입니다")
            return False
        return confirm_ok_cancel(
            self, PHOTO_COPY_TITLE, "Pub 폴더로 복사하시겠습니까?"
        )

    def _start_folder_copy(self, folder: Path) -> None:
        """확인을 받은 뒤 폴더를 별도 스레드에서 같은 위치의 Pub 폴더로 복사한다."""
        if not self._confirm_pub_copy():
            return
        self._start_copy(partial(copy_folder_to_pub, folder, self._project), PUB_FOLDER)

    def _start_copy(
        self, copy_task: Callable[[], PhotoCopySummary], folder_name: str
    ) -> None:
        """복사 작업을 별도 스레드에서 시작한다."""
        self._copy_folder_name = folder_name
        worker = PhotoCopyWorker(copy_task, self)
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
        message = _photo_copy_message(summary, self._copy_folder_name)
        if message:
            show_toast(self, message)
        if summary.failures:
            warn(self, PHOTO_COPY_TITLE, "\n".join(summary.failures))

    def _add_entry(self, name: str, is_folder: bool, icon: QIcon) -> QListWidgetItem:
        """항목 하나를 아이콘 목록과 자세히 표에 추가하고, 아이콘 항목을 반환한다."""
        item = QListWidgetItem(icon, name)
        item.setData(IS_FOLDER_ROLE, is_folder)
        # 폴더는 누르면 들어가므로 선택 표시를 하지 않는다. Work, Reference 안에서는 끌 수 있다.
        folder_flags = Qt.ItemFlag.ItemIsEnabled
        if self._drop_area_target() is not None:
            folder_flags |= Qt.ItemFlag.ItemIsDragEnabled
        if is_folder:
            item.setFlags(folder_flags)
        self._folder_list.addItem(item)

        details = read_entry_details(self._current / name, is_folder)
        detail_item = _DetailItem(name, is_folder, details, icon)
        if is_folder:
            detail_item.setFlags(folder_flags)
        self._detail_tree.addTopLevelItem(detail_item)
        self._detail_items[name] = detail_item
        return item

    def _start_thumbnail_loading(self, folder: Path, image_names: list[str]) -> None:
        """사진이 있으면 별도 스레드에서 썸네일 생성을 시작한다."""
        # 일반 파일은 썸네일을 만들 수 없으므로 읽지 않는다.
        image_names = [
            name for name in image_names if not _is_plain_file(name)
        ]
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
        """썸네일 생성을 중단하고 스레드가 끝나기를 기다린다. 종료나 파일 삭제 전에 호출한다."""
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

    def _create_folders_from_dialog(self, target: Path) -> None:
        """폴더 이름을 입력받아 target 안에 만들고 결과를 알린다."""
        dialog = MultiFolderDialog(target, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        names = dialog.folder_names()
        dialog.deleteLater()
        if not accepted:
            return

        summary = create_folders(names, target)
        location = "" if target == self._current else f"{target.name} 안에 "
        self._refresh()
        if summary.created_count:
            show_toast(self, f"{location}폴더 {summary.created_count}개를 만들었습니다")
        if summary.failures:
            warn(self, ADD_FOLDER_MENU_TEXT, "\n".join(summary.failures))

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
