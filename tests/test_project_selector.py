"""+ 버튼 흐름과 창 위치 테스트. 화면 없이 임시 폴더에서 실제 입력창을 조작한다."""

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from core.project_folders import SUBFOLDER_NAMES
from ui.message_boxes import confirm, warn
from ui.new_folder_dialog import NewFolderDialog
from ui.project_selector import ProjectSelector

OK = QDialogButtonBox.StandardButton.Ok
CANCEL = QDialogButtonBox.StandardButton.Cancel

DialogAction = Callable[[QDialog], None]


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def main_window(app: QApplication) -> Iterator[QWidget]:
    """위치를 비교할 기준이 되는 메인 창 역할의 위젯을 제공한다."""
    window = QWidget()
    window.setGeometry(100, 100, 960, 640)
    window.show()
    yield window
    window.close()


def _fill(name: str, check: bool = False, button=OK) -> DialogAction:
    """입력창에 값을 넣고, 체크박스를 설정한 뒤 버튼을 누르는 동작을 만든다."""

    def action(dialog: QDialog) -> None:
        """입력창을 조작한다."""
        dialog.findChild(QLineEdit).setText(name)
        dialog.findChild(QCheckBox).setChecked(check)
        dialog.findChild(QDialogButtonBox).button(button).click()

    return action


def _click_add(
    selector: ProjectSelector,
    actions: Iterable[DialogAction] = (),
    answer: bool = True,
) -> MagicMock:
    """+ 버튼을 누르고 입력창이 뜰 때마다 동작을 차례로 실행한다.

    준비한 동작이 없는데 입력창이 다시 뜨면 닫는다. 경고창 호출 기록을 반환한다.
    """
    queue = deque(actions)

    def run_next() -> None:
        """떠 있는 입력창에 다음 동작을 실행한다."""
        dialog = QApplication.activeModalWidget()
        if dialog is None:
            return
        if queue:
            queue.popleft()(dialog)
        else:
            dialog.reject()

    if answer:
        QTimer.singleShot(0, run_next)
    with (
        patch("ui.project_selector.confirm", return_value=answer),
        patch(
            "ui.project_selector.warn",
            side_effect=lambda *_: QTimer.singleShot(0, run_next),
        ) as warning,
    ):
        selector.findChild(QPushButton).click()
        QApplication.processEvents()
    return warning


def _combo_items(selector: ProjectSelector) -> list[str]:
    """콤보박스의 항목을 반환한다."""
    combo_box = selector.findChild(QComboBox)
    return [combo_box.itemText(index) for index in range(combo_box.count())]


def _assert_centered(dialog: QWidget, window: QWidget) -> None:
    """창의 중심이 메인 창의 중심과 일치하는지 확인한다."""
    delta = dialog.frameGeometry().center() - window.frameGeometry().center()
    assert abs(delta.x()) <= 1 and abs(delta.y()) <= 1


def test_checkbox_is_labeled_and_unchecked_by_default(
    app: QApplication, tmp_path: Path
) -> None:
    """체크박스 문구와 꺼진 기본값을 확인한다."""
    seen: dict[str, object] = {}

    def inspect(dialog: QDialog) -> None:
        """체크박스 상태를 기록하고 닫는다."""
        check_box = dialog.findChild(QCheckBox)
        seen["text"] = check_box.text()
        seen["checked"] = check_box.isChecked()
        dialog.reject()

    _click_add(ProjectSelector(tmp_path), [inspect])

    assert seen == {"text": "하위 폴더들 자동 구성", "checked": False}


def test_answering_no_creates_nothing(app: QApplication, tmp_path: Path) -> None:
    """첫 확인창에서 아니오를 누르면 아무것도 만들지 않는지 확인한다."""
    _click_add(ProjectSelector(tmp_path), answer=False)

    assert list(tmp_path.iterdir()) == []


def test_cancel_creates_nothing(app: QApplication, tmp_path: Path) -> None:
    """입력창에서 Cancel을 누르면 체크 여부와 상관없이 만들지 않는지 확인한다."""
    _click_add(ProjectSelector(tmp_path), [_fill("취소", True, CANCEL)])

    assert list(tmp_path.iterdir()) == []


def test_ok_without_check_creates_only_project_folder(
    app: QApplication, tmp_path: Path
) -> None:
    """체크하지 않고 OK를 누르면 이름으로 된 폴더만 만들고 선택하는지 확인한다."""
    selector = ProjectSelector(tmp_path)

    warning = _click_add(selector, [_fill("새폴더")])

    warning.assert_not_called()
    assert list((tmp_path / "새폴더").iterdir()) == []
    assert selector.findChild(QComboBox).currentText() == "새폴더"


def test_ok_with_check_creates_subfolders(
    app: QApplication, tmp_path: Path
) -> None:
    """체크하고 OK를 누르면 하위 폴더까지 만드는지 확인한다."""
    selector = ProjectSelector(tmp_path)

    warning = _click_add(selector, [_fill("새폴더", check=True)])

    warning.assert_not_called()
    created = {entry.name for entry in (tmp_path / "새폴더").iterdir()}
    assert created == set(SUBFOLDER_NAMES)
    assert _combo_items(selector) == ["새폴더"]


def test_duplicate_name_warns_and_keeps_existing_project(
    app: QApplication, tmp_path: Path
) -> None:
    """같은 이름이면 경고창을 띄우고 기존 프로젝트를 건드리지 않는지 확인한다."""
    (tmp_path / "기존").mkdir()
    selector = ProjectSelector(tmp_path)

    warning = _click_add(selector, [_fill("기존", check=True)])

    warning.assert_called_once()
    assert warning.call_args.args[2] == "'기존' 프로젝트가 이미 있습니다."
    assert list((tmp_path / "기존").iterdir()) == []


def test_duplicate_name_returns_to_dialog_with_input_kept(
    app: QApplication, tmp_path: Path
) -> None:
    """경고 후 입력창이 다시 뜨고, 입력한 이름과 체크 상태가 남아 있는지 확인한다."""
    (tmp_path / "기존").mkdir()
    seen: dict[str, object] = {}

    def inspect_and_rename(dialog: QDialog) -> None:
        """다시 뜬 입력창의 값을 기록하고, 이름을 고쳐 OK를 누른다."""
        seen["name"] = dialog.findChild(QLineEdit).text()
        seen["checked"] = dialog.findChild(QCheckBox).isChecked()
        _fill("새이름", check=True)(dialog)

    selector = ProjectSelector(tmp_path)

    warning = _click_add(
        selector, [_fill("기존", check=True), inspect_and_rename]
    )

    warning.assert_called_once()
    assert seen == {"name": "기존", "checked": True}
    created = {entry.name for entry in (tmp_path / "새이름").iterdir()}
    assert created == set(SUBFOLDER_NAMES)
    assert selector.findChild(QComboBox).currentText() == "새이름"


@pytest.mark.parametrize("name", ["", "a/b", ".숨김"])
def test_invalid_name_returns_to_dialog(
    app: QApplication, tmp_path: Path, name: str
) -> None:
    """잘못된 이름도 경고 후 입력창으로 돌아가, 이름을 고치면 만들어지는지 확인한다."""
    warning = _click_add(
        ProjectSelector(tmp_path), [_fill(name), _fill("올바른이름")]
    )

    warning.assert_called_once()
    assert [entry.name for entry in tmp_path.iterdir()] == ["올바른이름"]


def test_duplicate_then_cancel_creates_nothing(
    app: QApplication, tmp_path: Path
) -> None:
    """경고 후 돌아간 입력창에서 Cancel을 누르면 그대로 끝나는지 확인한다."""
    (tmp_path / "기존").mkdir()

    warning = _click_add(
        ProjectSelector(tmp_path), [_fill("기존"), _fill("다른이름", button=CANCEL)]
    )

    warning.assert_called_once()
    assert [entry.name for entry in tmp_path.iterdir()] == ["기존"]


def test_os_error_warns_closes_dialog_and_reloads_list(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """디스크 문제로 실패하면 입력창으로 돌아가지 않고, 남은 폴더를 표시하는지 확인한다."""
    original_mkdir = Path.mkdir

    def failing_mkdir(path: Path) -> None:
        """Image 폴더를 만들 때만 권한 오류를 낸다."""
        if path.name == "Image":
            raise PermissionError("권한 없음")
        original_mkdir(path)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    selector = ProjectSelector(tmp_path)
    unused_action = MagicMock()

    warning = _click_add(selector, [_fill("새폴더", check=True), unused_action])

    warning.assert_called_once()
    unused_action.assert_not_called()
    assert _combo_items(selector) == ["새폴더"]


def test_missing_root_warns_after_startup_and_disables_add_button(
    app: QApplication, tmp_path: Path
) -> None:
    """기준 경로가 없으면 위젯 생성 뒤에 경고하고, 경로를 만들지 않는지 확인한다."""
    root = tmp_path / "missing"

    with patch("ui.project_selector.warn") as warning:
        selector = ProjectSelector(root)
        warning.assert_not_called()
        QApplication.processEvents()

    warning.assert_called_once()
    assert not selector.findChild(QPushButton).isEnabled()
    assert not root.exists()


def test_warn_is_centered_and_closes_on_ok(main_window: QWidget) -> None:
    """경고창이 메인 창 가운데에 뜨고, OK를 누르면 닫히는지 확인한다."""
    seen: dict[str, bool] = {}

    def check_and_click_ok() -> None:
        """위치를 확인하고 OK를 누른다."""
        box = QApplication.activeModalWidget()
        _assert_centered(box, main_window)
        seen["native_disabled"] = box.testOption(
            QMessageBox.Option.DontUseNativeDialog
        )
        box.button(QMessageBox.StandardButton.Ok).click()

    QTimer.singleShot(0, check_and_click_ok)
    warn(main_window, "제목", "내용")

    assert seen == {"native_disabled": True}
    assert QApplication.activeModalWidget() is None


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        (QMessageBox.StandardButton.Yes, True),
        (QMessageBox.StandardButton.No, False),
    ],
)
def test_confirm_is_centered_and_returns_answer(
    main_window: QWidget, button: QMessageBox.StandardButton, expected: bool
) -> None:
    """확인창이 메인 창 가운데에 뜨고, 누른 버튼에 맞는 값을 반환하는지 확인한다."""

    def check_and_click() -> None:
        """위치를 확인하고 버튼을 누른다."""
        box = QApplication.activeModalWidget()
        _assert_centered(box, main_window)
        box.button(button).click()

    QTimer.singleShot(0, check_and_click)

    assert confirm(main_window, "제목", "내용") is expected


def test_new_folder_dialog_is_centered(main_window: QWidget) -> None:
    """이름 입력창이 메인 창 가운데에 뜨는지 확인한다."""

    def check_and_reject() -> None:
        """위치를 확인하고 닫는다."""
        dialog = QApplication.activeModalWidget()
        _assert_centered(dialog, main_window)
        dialog.reject()

    QTimer.singleShot(0, check_and_reject)

    assert NewFolderDialog(main_window).exec() == QDialog.DialogCode.Rejected
