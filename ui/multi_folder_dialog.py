"""만들 폴더의 이름을 목록으로 모아 한 번에 입력받는 창."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.project_folders import (
    FolderNameError,
    clean_folder_name,
    is_same_folder_name,
)
from ui.message_boxes import center_on_parent_window, warn

DIALOG_TITLE = "폴더 만들기"
# 항목의 이전 이름을 기억하는 위치. 잘못 고치면 이 이름으로 되돌린다.
PREVIOUS_NAME_ROLE = Qt.ItemDataRole.UserRole


class MultiFolderDialog(QDialog):
    """이름 입력란, + 버튼, 수정할 수 있는 목록, 선택 삭제, OK/Cancel로 이루어진 창."""

    def __init__(self, parent_folder: Path, parent: QWidget | None = None) -> None:
        """위젯을 위에서부터 차례로 배치한다."""
        super().__init__(parent)
        self.setWindowTitle(DIALOG_TITLE)
        self._parent_folder = parent_folder

        self._name_edit = QLineEdit()
        self._name_edit.returnPressed.connect(self._add_name)
        self._add_button = QPushButton("+")
        self._add_button.setFixedWidth(40)
        self._add_button.setAutoDefault(False)
        self._add_button.clicked.connect(self._add_name)

        self._name_list = QListWidget()
        self._name_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._name_list.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._name_list.itemChanged.connect(self._on_item_changed)
        self._name_list.itemSelectionChanged.connect(self._update_buttons)

        self._remove_button = QPushButton("선택 삭제")
        self._remove_button.setAutoDefault(False)
        self._remove_button.clicked.connect(self._remove_selected)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)

        input_row = QHBoxLayout()
        input_row.addWidget(self._name_edit, stretch=1)
        input_row.addWidget(self._add_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("폴더 이름"))
        layout.addLayout(input_row)
        layout.addWidget(self._name_list, stretch=1)
        layout.addWidget(QLabel("이름을 고치려면 목록에서 더블클릭하세요."))
        layout.addWidget(self._remove_button)
        layout.addWidget(self._button_box)

        self._update_buttons()

    def showEvent(self, event: QShowEvent) -> None:
        """크기가 정해진 뒤 창을 메인 창 가운데로 옮긴다."""
        super().showEvent(event)
        center_on_parent_window(self)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Enter는 이름 추가와 수정에만 쓰고, 창을 닫지 않게 한다."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return
        super().keyPressEvent(event)

    def folder_names(self) -> list[str]:
        """목록의 폴더 이름을 순서대로 반환한다."""
        return [
            self._name_list.item(index).text()
            for index in range(self._name_list.count())
        ]

    def accept(self) -> None:
        """이름을 고치던 중이면 그 내용을 먼저 반영한 뒤, 목록이 있으면 창을 닫는다."""
        self._name_list.setFocus()
        if self._name_list.count() > 0:
            super().accept()

    def _checked_name(self, name: str, ignored: QListWidgetItem | None = None) -> str:
        """이름을 검사해 정리한 이름을 반환한다. 쓸 수 없으면 FolderNameError가 발생한다."""
        cleaned_name = clean_folder_name(name)
        for index in range(self._name_list.count()):
            item = self._name_list.item(index)
            if item is not ignored and is_same_folder_name(item.text(), cleaned_name):
                raise FolderNameError(f"'{cleaned_name}'은 이미 목록에 있습니다.")
        if (self._parent_folder / cleaned_name).exists():
            raise FolderNameError(f"'{cleaned_name}'이 이 위치에 이미 있습니다.")
        return cleaned_name

    def _add_name(self) -> None:
        """입력한 이름을 검사해 목록에 추가하고 입력란을 비운다."""
        try:
            name = self._checked_name(self._name_edit.text())
        except FolderNameError as error:
            warn(self, DIALOG_TITLE, str(error))
            return

        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        item.setData(PREVIOUS_NAME_ROLE, name)
        self._name_list.blockSignals(True)
        self._name_list.addItem(item)
        self._name_list.blockSignals(False)
        self._name_edit.clear()
        self._name_edit.setFocus()
        self._update_buttons()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """고친 이름을 검사해 반영한다. 쓸 수 없는 이름이면 이전 이름으로 되돌린다."""
        previous_name = item.data(PREVIOUS_NAME_ROLE)
        if item.text() == previous_name:
            return
        self._name_list.blockSignals(True)
        try:
            name = self._checked_name(item.text(), ignored=item)
        except FolderNameError as error:
            item.setText(previous_name)
            self._name_list.blockSignals(False)
            warn(self, DIALOG_TITLE, str(error))
            return
        item.setText(name)
        item.setData(PREVIOUS_NAME_ROLE, name)
        self._name_list.blockSignals(False)

    def _remove_selected(self) -> None:
        """선택한 이름을 목록에서 뺀다."""
        for item in self._name_list.selectedItems():
            self._name_list.takeItem(self._name_list.row(item))
        self._update_buttons()

    def _update_buttons(self) -> None:
        """선택과 목록 상태에 맞게 삭제 버튼과 OK 버튼을 켜고 끈다."""
        self._remove_button.setEnabled(bool(self._name_list.selectedItems()))
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self._name_list.count() > 0
        )
