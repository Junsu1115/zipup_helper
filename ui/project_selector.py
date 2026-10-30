"""프로젝트 폴더를 고르고 새로 만드는 위젯."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from core.project_folders import (
    PROJECT_ROOT,
    FolderNameError,
    create_project_folder,
    list_project_folders,
)
from ui.message_boxes import confirm, warn
from ui.new_folder_dialog import NewFolderDialog

DIALOG_TITLE = "새 폴더"


class ProjectSelector(QWidget):
    """프로젝트 폴더 콤보박스와 새 폴더를 만드는 + 버튼."""

    def __init__(self, root: Path = PROJECT_ROOT, parent: QWidget | None = None) -> None:
        """위젯을 배치하고 root의 폴더 목록을 불러온다."""
        super().__init__(parent)
        self._root = root

        self._combo_box = QComboBox()
        self._add_button = QPushButton("+")
        self._add_button.setFixedWidth(40)
        self._add_button.setToolTip("새 폴더 만들기")
        self._add_button.clicked.connect(self._on_add_clicked)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo_box, stretch=1)
        layout.addWidget(self._add_button)

        self._reload_folders()

    def _reload_folders(self, selected_name: str | None = None) -> None:
        """폴더 목록을 다시 불러오고, 이름이 주어지면 그 폴더를 선택한다."""
        self._combo_box.clear()
        try:
            names = list_project_folders(self._root)
        except OSError as error:
            self._add_button.setEnabled(False)
            # 프로그램 시작 시에도 메인 창 가운데에 뜨도록, 메인 창이 표시된 뒤로 미룬다.
            QTimer.singleShot(0, partial(self._warn_load_failed, error))
            return

        self._add_button.setEnabled(True)
        self._combo_box.addItems(names)
        if selected_name is not None:
            self._combo_box.setCurrentText(selected_name)

    def _warn_load_failed(self, error: OSError) -> None:
        """폴더 목록을 읽지 못했다는 경고창을 띄운다."""
        warn(
            self,
            "폴더 목록 오류",
            f"폴더 목록을 읽지 못했습니다.\n{self._root}\n\n{error}",
        )

    def _on_add_clicked(self) -> None:
        """폴더를 만들지 묻고, 이름을 입력받아 폴더를 만든다.

        이름 때문에 만들 수 없으면 경고 후 입력 내용이 남아 있는 입력창으로 돌아간다.
        """
        if not confirm(self, DIALOG_TITLE, "새 폴더를 만드시겠습니까?"):
            return

        dialog = NewFolderDialog(self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                created_name = create_project_folder(
                    dialog.folder_name(), self._root, dialog.with_subfolders()
                )
            except FolderNameError as error:
                warn(self, DIALOG_TITLE, str(error))
                continue
            except OSError as error:
                warn(self, DIALOG_TITLE, f"폴더를 만들지 못했습니다.\n\n{error}")
                # 하위 폴더를 만들다 실패하면 일부 폴더가 남을 수 있어 목록을 다시 불러온다.
                self._reload_folders(selected_name=self._combo_box.currentText())
                return

            self._reload_folders(selected_name=created_name)
            return
