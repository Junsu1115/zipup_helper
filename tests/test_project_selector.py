"""+ 버튼 흐름과 창 위치 테스트. 화면 없이 임시 폴더에서 실제 입력창을 조작한다."""

import os
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from core.project_folders import SUBFOLDER_TREE
from ui.gc_guard import install_main_thread_gc
from ui.message_boxes import confirm, warn
from ui.new_folder_dialog import NewFolderDialog
from ui.project_selector import ProjectSelector
from ui.toast import TOAST_DURATION_MS, _Toast, show_toast

OK = QDialogButtonBox.StandardButton.Ok
CANCEL = QDialogButtonBox.StandardButton.Cancel
MODAL_TIMEOUT_MS = 3000

DialogAction = Callable[[QDialog], None]


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture(autouse=True)
def modal_watchdog(app: QApplication) -> Iterator[None]:
    """닫히지 않은 창을 닫아, 전체 테스트가 멈추지 않고 해당 테스트만 실패하게 한다."""
    stuck_titles: list[str] = []

    def close_stuck_dialog() -> None:
        """떠 있는 모달 창을 기록하고 닫는다."""
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            stuck_titles.append(dialog.windowTitle())
            dialog.close()

    timer = QTimer()
    timer.setInterval(MODAL_TIMEOUT_MS)
    timer.timeout.connect(close_stuck_dialog)
    timer.start()
    yield
    timer.stop()
    if stuck_titles:
        pytest.fail(f"창이 닫히지 않아 테스트가 멈췄습니다: {stuck_titles}")


@pytest.fixture
def main_window(app: QApplication) -> Iterator[QWidget]:
    """위치를 비교할 기준이 되는 메인 창 역할의 위젯을 제공한다."""
    window = QWidget()
    window.setGeometry(100, 100, 960, 640)
    window.show()
    yield window
    window.close()


def _tree_item(dialog: QDialog, name: str) -> QTreeWidgetItem:
    """트리에서 이름이 같은 최상위 항목을 찾아 반환한다."""
    tree = dialog.findChild(QTreeWidget)
    return tree.findItems(name, Qt.MatchFlag.MatchExactly)[0]


def _fill(
    name: str,
    check: bool = False,
    button=OK,
    uncheck: Iterable[str] = (),
) -> DialogAction:
    """입력창에 값을 넣고, 체크 항목을 설정한 뒤 버튼을 누르는 동작을 만든다."""

    def action(dialog: QDialog) -> None:
        """입력창을 조작한다."""
        dialog.findChild(QLineEdit).setText(name)
        dialog.findChild(QCheckBox).setChecked(check)
        for folder_name in uncheck:
            _tree_item(dialog, folder_name).setCheckState(0, Qt.CheckState.Unchecked)
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
    assert created == set(SUBFOLDER_TREE)
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
    assert created == set(SUBFOLDER_TREE)
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


def test_tree_follows_checkbox_and_shows_folder_structure(
    app: QApplication, tmp_path: Path
) -> None:
    """체크박스에 따라 트리가 나타나고, 폴더 구조와 체크 상태가 맞는지 확인한다."""
    seen: dict[str, object] = {}

    def inspect(dialog: QDialog) -> None:
        """체크박스를 켜고 끄며 트리 상태를 기록한다."""
        tree = dialog.findChild(QTreeWidget)
        check_box = dialog.findChild(QCheckBox)
        seen["before"] = tree.isVisible()
        check_box.setChecked(True)
        seen["on"] = tree.isVisible()
        document = _tree_item(dialog, "Document")
        seen["top"] = [
            (tree.topLevelItem(index).text(0), tree.topLevelItem(index).checkState(0))
            for index in range(tree.topLevelItemCount())
        ]
        seen["children"] = [
            document.child(index).text(0) for index in range(document.childCount())
        ]
        seen["child_has_checkbox"] = document.child(0).data(
            0, Qt.ItemDataRole.CheckStateRole
        )
        seen["expanded"] = document.isExpanded()
        check_box.setChecked(False)
        seen["off"] = tree.isVisible()
        dialog.reject()

    _click_add(ProjectSelector(tmp_path), [inspect])

    checked = Qt.CheckState.Checked
    assert seen == {
        "before": False,
        "on": True,
        "top": [
            ("Document", checked),
            ("Reference", checked),
            ("Work", checked),
            ("Pub", checked),
        ],
        "children": ["PPT_ORG", "PPT_Break"],
        "child_has_checkbox": None,
        "expanded": True,
        "off": False,
    }


def test_only_checked_tree_items_are_created(
    app: QApplication, tmp_path: Path
) -> None:
    """트리에서 체크한 폴더와 Document 아래 폴더만 만드는지 확인한다."""
    _click_add(
        ProjectSelector(tmp_path),
        [_fill("새폴더", check=True, uncheck=["Reference", "Pub"])],
    )

    project = tmp_path / "새폴더"
    assert {entry.name for entry in project.iterdir()} == {"Document", "Work"}
    children = {entry.name for entry in (project / "Document").iterdir()}
    assert children == {"PPT_ORG", "PPT_Break"}
    assert {entry.name for entry in (project / "Work").iterdir()} == {"Image", "Video"}


def test_unchecking_document_skips_ppt_folders(
    app: QApplication, tmp_path: Path
) -> None:
    """Document를 해제하면 PPT 폴더도 만들지 않는지 확인한다."""
    _click_add(
        ProjectSelector(tmp_path), [_fill("새폴더", check=True, uncheck=["Document"])]
    )

    created = {entry.name for entry in (tmp_path / "새폴더").iterdir()}
    assert created == {"Reference", "Work", "Pub"}


def test_unchecking_all_items_creates_only_project_folder(
    app: QApplication, tmp_path: Path
) -> None:
    """모두 해제하면 경고 없이 프로젝트 폴더만 만드는지 확인한다."""
    warning = _click_add(
        ProjectSelector(tmp_path),
        [_fill("새폴더", check=True, uncheck=list(SUBFOLDER_TREE))],
    )

    warning.assert_not_called()
    assert list((tmp_path / "새폴더").iterdir()) == []


def test_turning_checkbox_off_ignores_tree_selection(
    app: QApplication, tmp_path: Path
) -> None:
    """체크박스를 끄면 트리의 체크 상태와 상관없이 폴더만 만드는지 확인한다."""
    _click_add(ProjectSelector(tmp_path), [_fill("새폴더", check=False)])

    assert list((tmp_path / "새폴더").iterdir()) == []


def test_tree_selection_is_kept_after_duplicate_warning(
    app: QApplication, tmp_path: Path
) -> None:
    """같은 이름 경고 후 돌아온 입력창에 트리 선택이 남아 있는지 확인한다."""
    (tmp_path / "기존").mkdir()
    seen: dict[str, object] = {}

    def inspect_and_rename(dialog: QDialog) -> None:
        """트리 상태를 기록하고, 이름만 고쳐 OK를 누른다."""
        seen["visible"] = dialog.findChild(QTreeWidget).isVisible()
        seen["pub"] = _tree_item(dialog, "Pub").checkState(0)
        dialog.findChild(QLineEdit).setText("새이름")
        dialog.findChild(QDialogButtonBox).button(OK).click()

    _click_add(
        ProjectSelector(tmp_path),
        [_fill("기존", check=True, uncheck=["Pub"]), inspect_and_rename],
    )

    assert seen == {"visible": True, "pub": Qt.CheckState.Unchecked}
    created = {entry.name for entry in (tmp_path / "새이름").iterdir()}
    assert created == {"Document", "Reference", "Work"}


def _is_dimmed(item: QTreeWidgetItem) -> bool:
    """항목의 글자가 비활성 글자색으로 표시되는지 반환한다."""
    disabled_color = item.treeWidget().palette().color(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
    )
    brush = item.foreground(0)
    return brush.style() != Qt.BrushStyle.NoBrush and brush.color() == disabled_color


def test_unchecked_items_are_dimmed_and_restored(app: QApplication) -> None:
    """체크를 해제하면 항목과 하위 항목이 흐려지고, 다시 체크하면 돌아오는지 확인한다."""
    dialog = NewFolderDialog()
    document = _tree_item(dialog, "Document")
    pub = _tree_item(dialog, "Pub")
    work = _tree_item(dialog, "Work")
    children = [document.child(0), document.child(1)]
    pub_children = [pub.child(0), pub.child(1)]
    all_items = [document, pub, work, *children, *pub_children, work.child(0)]

    assert not any(map(_is_dimmed, all_items))

    document.setCheckState(0, Qt.CheckState.Unchecked)
    pub.setCheckState(0, Qt.CheckState.Unchecked)

    assert all(map(_is_dimmed, [document, pub, *children, *pub_children]))
    assert not _is_dimmed(work)
    assert not _is_dimmed(work.child(0))

    document.setCheckState(0, Qt.CheckState.Checked)

    assert not any(map(_is_dimmed, [document, *children]))
    assert _is_dimmed(pub)


def test_dimming_is_kept_after_duplicate_warning(
    app: QApplication, tmp_path: Path
) -> None:
    """경고 후 돌아온 입력창에서도 흐린 표시가 유지되는지 확인한다."""
    (tmp_path / "기존").mkdir()
    seen: dict[str, bool] = {}

    def inspect(dialog: QDialog) -> None:
        """Document와 하위 항목의 표시를 기록하고 닫는다."""
        document = _tree_item(dialog, "Document")
        seen["document"] = _is_dimmed(document)
        seen["child"] = _is_dimmed(document.child(0))
        dialog.reject()

    _click_add(
        ProjectSelector(tmp_path),
        [_fill("기존", check=True, uncheck=["Document"]), inspect],
    )

    assert seen == {"document": True, "child": True}


def test_dialog_stays_centered_when_tree_toggles(main_window: QWidget) -> None:
    """트리가 나타나고 사라질 때 창 크기가 바뀌어도 가운데를 유지하는지 확인한다."""
    widths: list[int] = []

    def toggle_and_check() -> None:
        """체크박스를 켜고 끄며 크기와 위치를 확인한다."""
        dialog = QApplication.activeModalWidget()
        check_box = dialog.findChild(QCheckBox)
        widths.append(dialog.width())
        check_box.setChecked(True)
        widths.append(dialog.width())
        _assert_centered(dialog, main_window)
        check_box.setChecked(False)
        widths.append(dialog.width())
        _assert_centered(dialog, main_window)
        dialog.reject()

    QTimer.singleShot(0, toggle_and_check)
    NewFolderDialog(main_window).exec()

    assert widths[1] > widths[0]
    assert widths[2] == widths[0]


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(20)
        waited_ms += 20
    return condition()


def test_external_changes_update_list(app: QApplication, tmp_path: Path) -> None:
    """프로그램 밖에서 폴더를 지우고, 만들고, 이름을 바꾸면 목록이 바뀌는지 확인한다."""
    (tmp_path / "가").mkdir()
    (tmp_path / "나").mkdir()
    selector = ProjectSelector(tmp_path)

    (tmp_path / "가").rmdir()
    assert _wait_until(lambda: _combo_items(selector) == ["나"])

    (tmp_path / "다").mkdir()
    assert _wait_until(lambda: _combo_items(selector) == ["나", "다"])

    (tmp_path / "다").rename(tmp_path / "라")
    assert _wait_until(lambda: _combo_items(selector) == ["나", "라"])


def test_refresh_keeps_selected_folder(app: QApplication, tmp_path: Path) -> None:
    """다른 폴더가 지워져도 선택 중인 폴더가 유지되는지 확인한다."""
    (tmp_path / "가").mkdir()
    (tmp_path / "나").mkdir()
    selector = ProjectSelector(tmp_path)
    selector.findChild(QComboBox).setCurrentText("나")

    (tmp_path / "가").rmdir()

    assert _wait_until(lambda: _combo_items(selector) == ["나"])
    assert selector.findChild(QComboBox).currentText() == "나"


def test_deleting_selected_folder_selects_first_item(
    app: QApplication, tmp_path: Path
) -> None:
    """선택 중인 폴더가 지워지면 첫 번째 폴더가 선택되는지 확인한다."""
    for name in ("가", "나", "다"):
        (tmp_path / name).mkdir()
    selector = ProjectSelector(tmp_path)
    selector.findChild(QComboBox).setCurrentText("다")

    (tmp_path / "다").rmdir()

    assert _wait_until(lambda: _combo_items(selector) == ["가", "나"])
    assert selector.findChild(QComboBox).currentText() == "가"


def test_created_folder_stays_selected_after_refresh(
    app: QApplication, tmp_path: Path
) -> None:
    """프로그램에서 만든 폴더가 변경 감지 후에도 선택되어 있는지 확인한다."""
    (tmp_path / "가").mkdir()
    selector = ProjectSelector(tmp_path)

    _click_add(selector, [_fill("나")])
    QTest.qWait(500)

    assert selector.findChild(QComboBox).currentText() == "나"


def test_repeated_changes_reload_only_once(
    app: QApplication, tmp_path: Path
) -> None:
    """변경 알림이 연달아 와도 목록을 한 번만 읽는지 확인한다."""
    selector = ProjectSelector(tmp_path)

    with patch(
        "ui.project_selector.list_project_folders", return_value=[]
    ) as list_folders:
        for _ in range(5):
            selector._watcher._on_directory_changed(str(tmp_path))
        QTest.qWait(400)

    # 앞선 테스트의 위젯이 남아 호출할 수 있으므로, 이 위젯의 호출만 센다.
    own_calls = [
        call for call in list_folders.call_args_list if call.args[0] == tmp_path
    ]
    assert len(own_calls) == 1


def test_removed_root_warns_once_and_disables_add_button(
    app: QApplication, tmp_path: Path
) -> None:
    """기준 폴더가 사라지면 경고를 한 번만 띄우고 + 버튼을 끄는지 확인한다."""
    root = tmp_path / "root"
    root.mkdir()
    selector = ProjectSelector(root)

    with patch("ui.project_selector.warn") as warning:
        root.rmdir()
        assert _wait_until(lambda: warning.called)
        selector._watcher._on_directory_changed(str(root))
        QTest.qWait(400)

    warning.assert_called_once()
    assert not selector.findChild(QPushButton).isEnabled()
    assert _combo_items(selector) == []
    assert not root.exists()


def _record_projects(selector: ProjectSelector) -> list[Path | None]:
    """project_changed로 받은 값을 차례로 담는 목록을 반환한다."""
    received: list[Path | None] = []
    selector.project_changed.connect(received.append)
    return received


def test_current_project_is_first_folder_or_none(
    app: QApplication, tmp_path: Path
) -> None:
    """처음 선택된 프로젝트가 첫 폴더이고, 폴더가 없으면 None인지 확인한다."""
    assert ProjectSelector(tmp_path).current_project() is None

    (tmp_path / "가").mkdir()

    assert ProjectSelector(tmp_path).current_project() == tmp_path / "가"


def test_choosing_project_sends_signal(app: QApplication, tmp_path: Path) -> None:
    """콤보박스에서 다른 프로젝트를 고르면 신호를 보내는지 확인한다."""
    (tmp_path / "가").mkdir()
    (tmp_path / "나").mkdir()
    selector = ProjectSelector(tmp_path)
    received = _record_projects(selector)

    selector.findChild(QComboBox).setCurrentText("나")

    assert received == [tmp_path / "나"]
    assert selector.current_project() == tmp_path / "나"


def test_refresh_without_selection_change_sends_no_signal(
    app: QApplication, tmp_path: Path
) -> None:
    """다른 폴더가 지워져 목록만 바뀌면 신호를 보내지 않는지 확인한다."""
    (tmp_path / "가").mkdir()
    (tmp_path / "나").mkdir()
    selector = ProjectSelector(tmp_path)
    selector.findChild(QComboBox).setCurrentText("나")
    received = _record_projects(selector)

    (tmp_path / "가").rmdir()

    assert _wait_until(lambda: _combo_items(selector) == ["나"])
    assert received == []


def test_deleting_selected_project_sends_signal_once(
    app: QApplication, tmp_path: Path
) -> None:
    """선택한 프로젝트가 지워지면 새 선택을 한 번만 알리는지 확인한다."""
    (tmp_path / "가").mkdir()
    (tmp_path / "나").mkdir()
    selector = ProjectSelector(tmp_path)
    selector.findChild(QComboBox).setCurrentText("나")
    received = _record_projects(selector)

    (tmp_path / "나").rmdir()

    assert _wait_until(lambda: _combo_items(selector) == ["가"])
    assert received == [tmp_path / "가"]


def test_deleting_last_project_sends_none(
    app: QApplication, tmp_path: Path
) -> None:
    """마지막 프로젝트가 지워지면 None을 알리는지 확인한다."""
    (tmp_path / "가").mkdir()
    selector = ProjectSelector(tmp_path)
    received = _record_projects(selector)

    (tmp_path / "가").rmdir()

    assert _wait_until(lambda: received == [None])
    assert selector.current_project() is None


def test_created_project_sends_signal(app: QApplication, tmp_path: Path) -> None:
    """+ 버튼으로 만든 프로젝트를 알리는지 확인한다."""
    (tmp_path / "가").mkdir()
    selector = ProjectSelector(tmp_path)
    received = _record_projects(selector)

    _click_add(selector, [_fill("나")])

    assert received == [tmp_path / "나"]


def test_confirm_places_yes_left_of_no(main_window: QWidget) -> None:
    """확인창에서 Yes가 No의 왼쪽에 있는지 확인한다."""
    seen: dict[str, int] = {}

    def record_and_click() -> None:
        """버튼 위치를 기록하고 닫는다."""
        box = QApplication.activeModalWidget()
        seen["yes"] = box.button(QMessageBox.StandardButton.Yes).x()
        seen["no"] = box.button(QMessageBox.StandardButton.No).x()
        box.button(QMessageBox.StandardButton.No).click()

    QTimer.singleShot(0, record_and_click)
    confirm(main_window, "제목", "내용")

    assert seen["yes"] < seen["no"]


def test_toast_shows_after_folder_created(
    main_window: QWidget, tmp_path: Path
) -> None:
    """폴더가 만들어지면 메인 창 하단 가운데에 알림이 뜨는지 확인한다."""
    selector = ProjectSelector(tmp_path, main_window)

    _click_add(selector, [_fill("새폴더")])

    toast = main_window.findChild(_Toast)
    assert toast.isVisible()
    assert toast.text() == "폴더가 생성되었습니다"
    delta_x = toast.geometry().center().x() - main_window.rect().center().x()
    assert abs(delta_x) <= 1
    assert toast.geometry().bottom() < main_window.height()
    assert toast.geometry().top() > main_window.height() // 2


@pytest.mark.parametrize(
    "actions",
    [
        pytest.param([_fill("기존")], id="같은 이름"),
        pytest.param([_fill("새폴더", button=CANCEL)], id="Cancel"),
    ],
)
def test_toast_does_not_show_without_creation(
    main_window: QWidget, tmp_path: Path, actions: list[DialogAction]
) -> None:
    """폴더를 만들지 않았으면 알림이 뜨지 않는지 확인한다."""
    (tmp_path / "기존").mkdir()
    selector = ProjectSelector(tmp_path, main_window)

    _click_add(selector, actions)

    assert main_window.findChild(_Toast) is None


def test_toast_duration_is_two_seconds() -> None:
    """알림 표시 시간이 2초인지 확인한다."""
    assert TOAST_DURATION_MS == 2000


def test_toast_disappears_by_itself(main_window: QWidget) -> None:
    """정해진 시간이 지나면 알림이 저절로 사라지는지 확인한다."""
    with patch("ui.toast.TOAST_DURATION_MS", 50):
        show_toast(main_window, "알림")

    toast = main_window.findChild(_Toast)
    assert toast.isVisible()
    QTest.qWait(200)
    assert not toast.isVisible()


def test_toast_restarts_time_when_shown_again(main_window: QWidget) -> None:
    """다시 띄우면 알림을 하나만 쓰고, 시간을 새로 세는지 확인한다."""
    with patch("ui.toast.TOAST_DURATION_MS", 300):
        show_toast(main_window, "첫 번째")
        QTest.qWait(200)
        show_toast(main_window, "두 번째")
        QTest.qWait(200)

    toast = main_window.findChild(_Toast)
    assert len(main_window.findChildren(_Toast)) == 1
    assert toast.isVisible()
    assert toast.text() == "두 번째"
    QTest.qWait(300)
    assert not toast.isVisible()


def test_new_folder_dialog_is_centered(main_window: QWidget) -> None:
    """이름 입력창이 메인 창 가운데에 뜨는지 확인한다."""

    def check_and_reject() -> None:
        """위치를 확인하고 닫는다."""
        dialog = QApplication.activeModalWidget()
        _assert_centered(dialog, main_window)
        dialog.reject()

    QTimer.singleShot(0, check_and_reject)

    assert NewFolderDialog(main_window).exec() == QDialog.DialogCode.Rejected
