"""새 폴더의 이름과 하위 폴더 구성 여부를 입력받는 창."""

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ui.message_boxes import center_on_parent_window


class NewFolderDialog(QDialog):
    """이름 입력란, 하위 폴더 체크박스, OK/Cancel 버튼으로 이루어진 입력창."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """입력창의 위젯을 위에서부터 차례로 배치한다."""
        super().__init__(parent)
        self.setWindowTitle("새 폴더")

        self._name_edit = QLineEdit()
        self._subfolder_check_box = QCheckBox("하위 폴더들 자동 구성")

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("폴더 이름을 입력하세요."))
        layout.addWidget(self._name_edit)
        layout.addWidget(self._subfolder_check_box)
        layout.addWidget(button_box)

    def showEvent(self, event: QShowEvent) -> None:
        """크기가 정해진 뒤 창을 메인 창 가운데로 옮긴다."""
        super().showEvent(event)
        center_on_parent_window(self)

    def folder_name(self) -> str:
        """입력된 폴더 이름을 반환한다."""
        return self._name_edit.text()

    def with_subfolders(self) -> bool:
        """하위 폴더 자동 구성을 선택했는지 반환한다."""
        return self._subfolder_check_box.isChecked()
