"""프로젝트 안의 폴더를 큰 아이콘으로 보여주고, 눌러서 들어가는 화면."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.project_folders import list_project_folders
from ui.folder_watcher import DebouncedFolderWatcher
from ui.message_boxes import warn

ICON_SIZE = QSize(96, 96)
GRID_SIZE = QSize(140, 140)
PATH_SEPARATOR = " / "


class FolderBrowser(QWidget):
    """뒤로 가기 버튼, 현재 경로, 폴더 아이콘 목록으로 이루어진 화면."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """위젯을 배치한다. 프로젝트는 set_project()로 지정한다."""
        super().__init__(parent)
        self._project: Path | None = None
        self._current: Path | None = None
        self._folder_icon = QFileIconProvider().icon(
            QFileIconProvider.IconType.Folder
        )

        self._back_button = QPushButton("← 뒤로")
        self._back_button.clicked.connect(self._on_back_clicked)
        self._path_label = QLabel()

        self._folder_list = QListWidget()
        self._folder_list.setViewMode(QListWidget.ViewMode.IconMode)
        self._folder_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._folder_list.setMovement(QListWidget.Movement.Static)
        self._folder_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._folder_list.setIconSize(ICON_SIZE)
        self._folder_list.setGridSize(GRID_SIZE)
        self._folder_list.setWordWrap(True)
        self._folder_list.itemClicked.connect(self._on_item_clicked)

        self._empty_label = QLabel("폴더가 없습니다")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header_layout = QHBoxLayout()
        header_layout.addWidget(self._back_button)
        header_layout.addWidget(self._path_label, stretch=1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(header_layout)
        layout.addWidget(self._folder_list, stretch=1)
        layout.addWidget(self._empty_label, stretch=1)

        self._watcher = DebouncedFolderWatcher(self)
        self._watcher.changed.connect(self._refresh)

        self._show_no_folder()

    def set_project(self, project: Path | None) -> None:
        """표시할 프로젝트를 바꾸고, 그 프로젝트의 맨 위를 보여준다."""
        self._project = project
        if project is None or not self._navigate_to(project):
            self._show_no_folder()

    def _navigate_to(self, folder: Path) -> bool:
        """폴더로 이동하고 성공 여부를 반환한다. 읽지 못하면 경고하고 화면을 유지한다."""
        try:
            names = list_project_folders(folder)
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

        self._current = folder
        self._show_folders(names)
        self._update_header()
        self._watcher.set_paths(self._watch_targets())
        return True

    def _show_folders(self, names: list[str]) -> None:
        """폴더 아이콘 목록을 그리고, 폴더가 없으면 안내 문구를 보여준다."""
        self._folder_list.clear()
        for name in names:
            self._folder_list.addItem(QListWidgetItem(self._folder_icon, name))
        self._folder_list.setVisible(bool(names))
        self._empty_label.setVisible(not names)

    def _show_no_folder(self) -> None:
        """표시할 폴더가 없는 상태로 화면을 바꾸고 감시를 멈춘다."""
        self._current = None
        self._show_folders([])
        self._update_header()
        self._watcher.set_paths([])

    def _update_header(self) -> None:
        """현재 경로와 뒤로 가기 버튼 상태를 갱신한다. 프로젝트 맨 위에서는 버튼을 끈다."""
        if self._current is None or self._project is None:
            self._path_label.clear()
            self._back_button.setEnabled(False)
            return
        relative_parts = self._current.relative_to(self._project).parts
        self._path_label.setText(
            PATH_SEPARATOR.join((self._project.name, *relative_parts))
        )
        self._back_button.setEnabled(self._current != self._project)

    def _watch_targets(self) -> list[Path]:
        """프로젝트부터 현재 폴더까지의 경로를 반환한다. 중간 폴더가 사라져도 알기 위해서다."""
        targets = [self._current]
        while targets[-1] != self._project:
            targets.append(targets[-1].parent)
        return targets

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        """누른 폴더로 들어간다."""
        self._navigate_to(self._current / item.text())

    def _on_back_clicked(self) -> None:
        """한 단계 위 폴더로 올라간다."""
        self._navigate_to(self._current.parent)

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
