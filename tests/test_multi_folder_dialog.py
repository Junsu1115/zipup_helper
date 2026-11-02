"""여러 폴더 만들기 창 테스트. 임시 폴더만 사용한다."""

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QPushButton,
    QWidget,
)

from ui.gc_guard import install_main_thread_gc
from ui.multi_folder_dialog import MultiFolderDialog

OK = QDialogButtonBox.StandardButton.Ok
CANCEL = QDialogButtonBox.StandardButton.Cancel


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture
def warning() -> Iterator[MagicMock]:
    """경고창을 가짜로 바꾼다."""
    with patch("ui.multi_folder_dialog.warn") as warn_mock:
        yield warn_mock


@pytest.fixture
def dialog(
    app: QApplication, tmp_path: Path, warning: MagicMock
) -> Iterator[MultiFolderDialog]:
    """화면에 표시된 창을 제공한다."""
    widget = MultiFolderDialog(tmp_path)
    widget.show()
    yield widget
    widget.close()


def _add(dialog: MultiFolderDialog, name: str) -> None:
    """이름을 입력하고 + 버튼을 누른다."""
    dialog.findChild(QLineEdit).setText(name)
    next(
        button for button in dialog.findChildren(QPushButton) if button.text() == "+"
    ).click()


def _remove_button(dialog: MultiFolderDialog) -> QPushButton:
    """선택 삭제 버튼을 반환한다."""
    return next(
        button
        for button in dialog.findChildren(QPushButton)
        if button.text() == "선택 삭제"
    )


def _box_button(dialog: MultiFolderDialog, button: QDialogButtonBox.StandardButton):
    """OK 또는 Cancel 버튼을 반환한다."""
    return dialog.findChild(QDialogButtonBox).button(button)


def test_plus_adds_names_in_order_and_clears_input(dialog: MultiFolderDialog) -> None:
    """+를 누르면 목록 아래로 하나씩 추가되고 입력란이 비워지는지 확인한다."""
    _add(dialog, "시안1")
    _add(dialog, "  시안2  ")

    assert dialog.folder_names() == ["시안1", "시안2"]
    assert dialog.findChild(QLineEdit).text() == ""


def test_enter_adds_name_without_closing_dialog(dialog: MultiFolderDialog) -> None:
    """입력란에서 Enter를 누르면 추가만 되고 창은 닫히지 않는지 확인한다."""
    name_edit = dialog.findChild(QLineEdit)
    name_edit.setText("시안1")

    QTest.keyClick(name_edit, Qt.Key.Key_Return)

    assert dialog.folder_names() == ["시안1"]
    assert dialog.isVisible()

    QTest.keyClick(name_edit, Qt.Key.Key_Return)
    assert dialog.isVisible()


def test_enter_on_list_does_not_close_dialog(dialog: MultiFolderDialog) -> None:
    """목록에서 Enter를 눌러도 창이 닫히지 않는지 확인한다."""
    _add(dialog, "시안1")
    name_list = dialog.findChild(QListWidget)
    name_list.setCurrentRow(0)

    QTest.keyClick(name_list, Qt.Key.Key_Return)
    QApplication.processEvents()

    assert dialog.isVisible()


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a:b", "a?b", ".숨김", "CON", "끝."])
def test_invalid_name_is_not_added(
    dialog: MultiFolderDialog, warning: MagicMock, name: str
) -> None:
    """잘못된 이름은 경고하고 목록에 넣지 않는지 확인한다."""
    _add(dialog, name)

    warning.assert_called_once()
    assert dialog.folder_names() == []


def test_duplicate_in_list_is_not_added(
    dialog: MultiFolderDialog, warning: MagicMock
) -> None:
    """목록에 있는 이름은 대소문자가 달라도 넣지 않는지 확인한다."""
    _add(dialog, "Sian")
    _add(dialog, "sian")

    warning.assert_called_once()
    assert dialog.folder_names() == ["Sian"]


def test_existing_folder_is_not_added(
    dialog: MultiFolderDialog, tmp_path: Path, warning: MagicMock
) -> None:
    """이 위치에 이미 있는 폴더는 넣지 않는지 확인한다."""
    (tmp_path / "기존").mkdir()

    _add(dialog, "기존")

    warning.assert_called_once()
    assert dialog.folder_names() == []


def test_items_are_editable_by_double_click(dialog: MultiFolderDialog) -> None:
    """목록 항목을 더블클릭해 고칠 수 있게 설정되어 있는지 확인한다."""
    _add(dialog, "시안1")
    name_list = dialog.findChild(QListWidget)

    assert name_list.item(0).flags() & Qt.ItemFlag.ItemIsEditable
    assert (
        name_list.editTriggers() & QAbstractItemView.EditTrigger.DoubleClicked
    )


def test_editing_item_changes_name(dialog: MultiFolderDialog) -> None:
    """목록에서 고친 이름이 앞뒤 공백 없이 반영되는지 확인한다."""
    _add(dialog, "시안1")
    item = dialog.findChild(QListWidget).item(0)

    item.setText("  최종안  ")

    assert dialog.folder_names() == ["최종안"]


@pytest.mark.parametrize("name", ["", "a/b", "CON", "시안2"])
def test_invalid_edit_restores_previous_name(
    dialog: MultiFolderDialog, warning: MagicMock, name: str
) -> None:
    """쓸 수 없거나 겹치는 이름으로 고치면 경고하고 되돌리는지 확인한다."""
    _add(dialog, "시안1")
    _add(dialog, "시안2")
    item = dialog.findChild(QListWidget).item(0)

    item.setText(name)

    warning.assert_called_once()
    assert dialog.folder_names() == ["시안1", "시안2"]


def test_edit_to_same_name_with_other_case_is_allowed(
    dialog: MultiFolderDialog, warning: MagicMock
) -> None:
    """자기 자신과만 겹치는 수정은 허용되는지 확인한다."""
    _add(dialog, "sian")
    item = dialog.findChild(QListWidget).item(0)

    item.setText("SIAN")

    warning.assert_not_called()
    assert dialog.folder_names() == ["SIAN"]


def test_edit_in_progress_is_applied_when_ok_is_clicked(
    dialog: MultiFolderDialog,
) -> None:
    """이름을 고치는 중에 OK를 눌러도 고친 내용이 반영되는지 확인한다."""
    _add(dialog, "시안1")
    name_list = dialog.findChild(QListWidget)
    name_list.editItem(name_list.item(0))
    QApplication.processEvents()
    editor = name_list.findChild(QLineEdit)
    editor.setText("최종안")

    _box_button(dialog, OK).click()

    assert dialog.folder_names() == ["최종안"]
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_remove_deletes_only_selected(dialog: MultiFolderDialog) -> None:
    """선택한 이름만 지워지고, 선택이 없으면 삭제 버튼이 꺼지는지 확인한다."""
    for name in ("가", "나", "다"):
        _add(dialog, name)
    name_list = dialog.findChild(QListWidget)
    assert not _remove_button(dialog).isEnabled()

    name_list.item(0).setSelected(True)
    name_list.item(2).setSelected(True)
    assert _remove_button(dialog).isEnabled()
    _remove_button(dialog).click()

    assert dialog.folder_names() == ["나"]
    assert not _remove_button(dialog).isEnabled()


def test_ok_is_enabled_only_with_names(dialog: MultiFolderDialog) -> None:
    """목록이 비면 OK를 누를 수 없는지 확인한다."""
    assert not _box_button(dialog, OK).isEnabled()

    _add(dialog, "시안1")
    assert _box_button(dialog, OK).isEnabled()

    dialog.findChild(QListWidget).item(0).setSelected(True)
    _remove_button(dialog).click()
    assert not _box_button(dialog, OK).isEnabled()


@pytest.mark.parametrize(
    ("button", "expected"),
    [(OK, QDialog.DialogCode.Accepted), (CANCEL, QDialog.DialogCode.Rejected)],
)
def test_buttons_close_dialog_and_center_on_window(
    app: QApplication,
    tmp_path: Path,
    warning: MagicMock,
    button: QDialogButtonBox.StandardButton,
    expected: QDialog.DialogCode,
) -> None:
    """버튼을 누르면 닫히고, 메인 창 가운데에 뜨는지 확인한다."""
    window = QWidget()
    window.setGeometry(100, 100, 960, 640)
    window.show()
    dialog = MultiFolderDialog(tmp_path, window)
    _add(dialog, "시안1")
    deltas = []

    def check_and_click() -> None:
        """위치를 기록하고 버튼을 누른다."""
        deltas.append(dialog.frameGeometry().center() - window.frameGeometry().center())
        _box_button(dialog, button).click()

    QTimer.singleShot(0, check_and_click)

    assert dialog.exec() == expected
    assert abs(deltas[0].x()) <= 1 and abs(deltas[0].y()) <= 1
    assert list(tmp_path.iterdir()) == []
    window.close()
