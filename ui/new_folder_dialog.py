"""새 폴더의 이름과 만들 하위 폴더를 입력받는 창."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QPalette, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project_folders import SUBFOLDER_TREE
from ui.message_boxes import center_on_parent_window

TREE_WIDTH = 180
# 체크박스가 있는 상위 폴더의 글자보다 하위 폴더가 안쪽에 오도록 하는 들여쓰기.
TREE_INDENTATION = 44


def _sync_item_dimming(item: QTreeWidgetItem) -> None:
    """체크가 해제된 항목과 그 아래 항목의 글자를 흐리게, 체크되면 원래대로 표시한다."""
    tree = item.treeWidget()
    if item.checkState(0) == Qt.CheckState.Checked:
        brush = QBrush()
    else:
        brush = QBrush(
            tree.palette().color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
            )
        )

    # 글자색을 바꿀 때 itemChanged가 다시 발생하지 않도록 잠시 막는다.
    was_blocked = tree.blockSignals(True)
    item.setForeground(0, brush)
    for index in range(item.childCount()):
        item.child(index).setForeground(0, brush)
    tree.blockSignals(was_blocked)


def _build_subfolder_tree() -> QTreeWidget:
    """SUBFOLDER_TREE로 트리를 만든다. 체크박스는 선택할 수 있는 폴더에만 표시한다."""
    tree = QTreeWidget()
    tree.setHeaderHidden(True)
    tree.setRootIsDecorated(False)
    tree.setIndentation(TREE_INDENTATION)
    tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
    tree.setFixedWidth(TREE_WIDTH)

    for name, child_names in SUBFOLDER_TREE.items():
        item = QTreeWidgetItem(tree, [name])
        item.setCheckState(0, Qt.CheckState.Checked)
        for child_name in child_names:
            child = QTreeWidgetItem(item, [child_name])
            child.setFlags(Qt.ItemFlag.ItemIsEnabled)

    tree.expandAll()
    tree.setItemsExpandable(False)
    tree.itemChanged.connect(_sync_item_dimming)
    return tree


class NewFolderDialog(QDialog):
    """이름 입력란, 하위 폴더 체크박스와 트리, OK/Cancel 버튼으로 이루어진 입력창."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """왼쪽에 입력란, 오른쪽에 트리, 아래에 버튼을 배치한다."""
        super().__init__(parent)
        self.setWindowTitle("새 폴더")

        self._name_edit = QLineEdit()
        self._subfolder_check_box = QCheckBox("하위 폴더들 자동 구성")
        self._subfolder_check_box.toggled.connect(self._on_subfolder_toggled)

        self._subfolder_tree = _build_subfolder_tree()
        self._subfolder_tree.setVisible(False)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        input_layout = QVBoxLayout()
        input_layout.addWidget(QLabel("프로젝트 이름을 입력하세요."))
        input_layout.addWidget(self._name_edit)
        input_layout.addWidget(self._subfolder_check_box)
        input_layout.addStretch()

        content_layout = QHBoxLayout()
        content_layout.addLayout(input_layout)
        content_layout.addWidget(self._subfolder_tree)

        layout = QVBoxLayout(self)
        layout.addLayout(content_layout)
        layout.addWidget(button_box)

    def showEvent(self, event: QShowEvent) -> None:
        """크기가 정해진 뒤 창을 메인 창 가운데로 옮긴다."""
        super().showEvent(event)
        center_on_parent_window(self)

    def _on_subfolder_toggled(self, checked: bool) -> None:
        """트리를 보이거나 숨기고, 바뀐 크기에 맞춰 창을 다시 가운데로 옮긴다."""
        self._subfolder_tree.setVisible(checked)
        # 트리가 사라진 뒤 창이 줄어들 수 있도록 레이아웃을 먼저 갱신한다.
        self.layout().activate()
        self.adjustSize()
        center_on_parent_window(self)

    def folder_name(self) -> str:
        """입력된 폴더 이름을 반환한다."""
        return self._name_edit.text()

    def selected_subfolders(self) -> list[str]:
        """만들 하위 폴더 이름을 반환한다. 자동 구성을 끄면 빈 목록을 반환한다."""
        if not self._subfolder_check_box.isChecked():
            return []
        items = (
            self._subfolder_tree.topLevelItem(index)
            for index in range(self._subfolder_tree.topLevelItemCount())
        )
        return [
            item.text(0)
            for item in items
            if item.checkState(0) == Qt.CheckState.Checked
        ]
