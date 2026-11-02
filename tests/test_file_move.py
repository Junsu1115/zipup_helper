"""Work, Reference 안에서 끌어서 옮기기 테스트. 임시 폴더와 가짜 드래그 이벤트를 사용한다."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QTreeWidget, QWidget

from core.file_move import move_within_target
from core.folder_contents import list_all_files, read_entry_details
from ui.file_drag import build_file_mime_data
from ui.folder_browser import FolderBrowser, ViewMode
from ui.gc_guard import install_main_thread_gc
from ui.toast import _Toast


def _write_files(folder: Path, *names: str) -> list[Path]:
    """내용이 파일 이름인 파일들을 만들어 경로를 반환한다."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / name for name in names]
    for path in paths:
        path.write_text(path.name)
    return paths


def _names(folder: Path) -> set[str]:
    """폴더 안의 항목 이름을 반환한다."""
    return {entry.name for entry in folder.iterdir()}


def test_move_file_into_folder_removes_original(tmp_path: Path) -> None:
    """파일을 옮기면 대상에 생기고 원래 위치에서 사라지는지 확인한다."""
    source = _write_files(tmp_path / "Work", "a.psd")[0]
    destination = tmp_path / "Work" / "시안"
    destination.mkdir()

    summary = move_within_target([source], destination, tmp_path)

    assert summary.moved_count == 1
    assert not source.exists()
    assert (destination / "a.psd").read_text() == "a.psd"


def test_move_folder_with_contents(tmp_path: Path) -> None:
    """폴더를 안의 내용과 함께 옮기는지 확인한다."""
    folder = tmp_path / "Reference" / "자료"
    _write_files(folder / "내부", "a.png")
    destination = tmp_path / "Reference" / "보관"
    destination.mkdir()

    summary = move_within_target([folder], destination, tmp_path)

    assert summary.moved_count == 1
    assert not folder.exists()
    assert (destination / "자료" / "내부" / "a.png").exists()


def test_move_does_not_overwrite_same_name(tmp_path: Path) -> None:
    """같은 이름이 있으면 덮어쓰지 않고 원래 파일을 남기는지 확인한다."""
    source = _write_files(tmp_path / "Work", "a.png")[0]
    existing = _write_files(tmp_path / "Work" / "시안", "a.png")[0]
    existing.write_text("기존")

    summary = move_within_target([source], existing.parent, tmp_path)

    assert summary.existing_count == 1
    assert source.exists()
    assert existing.read_text() == "기존"


def test_move_folder_into_itself_is_refused(tmp_path: Path) -> None:
    """폴더를 자기 자신이나 그 안으로 옮기지 않는지 확인한다."""
    folder = tmp_path / "Work" / "시안"
    inner = folder / "내부"
    inner.mkdir(parents=True)

    for destination in (folder, inner):
        summary = move_within_target([folder], destination, tmp_path)
        assert summary.moved_count == 0
        assert len(summary.failures) == 1
    assert inner.is_dir()


def test_move_to_same_folder_does_nothing(tmp_path: Path) -> None:
    """이미 그 폴더에 있는 파일은 그대로 두는지 확인한다."""
    source = _write_files(tmp_path / "Work" / "시안", "a.png")[0]

    summary = move_within_target([source], source.parent, tmp_path)

    assert summary.moved_count == 0
    assert summary.existing_count == 0
    assert summary.failures == []
    assert source.exists()


@pytest.mark.parametrize(
    ("source_folder", "destination"),
    [
        ("Work", "Reference/시안"),
        ("Reference", "Work/시안"),
        ("Pub", "Work/시안"),
        ("Work", "Pub/시안"),
        ("Document", "Work/시안"),
    ],
)
def test_move_across_folders_is_refused(
    tmp_path: Path, source_folder: str, destination: str
) -> None:
    """Work와 Reference 사이, 그 밖의 폴더와는 옮기지 않는지 확인한다."""
    source = _write_files(tmp_path / source_folder, "a.png")[0]
    target = tmp_path / destination
    target.mkdir(parents=True)

    summary = move_within_target([source], target, tmp_path)

    assert summary.moved_count == 0
    assert len(summary.failures) == 1
    assert source.exists()


def test_list_all_files_includes_any_type_and_skips_hidden(tmp_path: Path) -> None:
    """모든 형식의 파일을 정렬해 반환하고, 숨김 파일과 폴더는 빼는지 확인한다."""
    _write_files(tmp_path, "b.psd", "a.txt", "확장자없음", ".DS_Store")
    (tmp_path / "폴더").mkdir()

    assert list_all_files(tmp_path) == ["a.txt", "b.psd", "확장자없음"]


@pytest.mark.parametrize(
    ("name", "kind"),
    [("a.psd", "PSD 파일"), ("a.PNG", "PNG 이미지"), ("a.mp4", "MP4 동영상"), ("없음", "파일")],
)
def test_entry_kind_describes_plain_files(
    tmp_path: Path, name: str, kind: str
) -> None:
    """일반 파일의 종류 문구가 맞는지 확인한다."""
    assert read_entry_details(tmp_path / name, is_folder=False).kind == kind


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Work, Reference에 파일과 폴더가 있는 프로젝트를 만들어 반환한다."""
    project_path = tmp_path / "project"
    for root in ("Work", "Reference"):
        _write_files(project_path / root, "a.png", "b.psd")
        _write_files(project_path / root / "시안", "내부.png")
        (project_path / root / "보관").mkdir()
    _write_files(project_path / "Document", "c.png", "메모.txt")
    (project_path / "Document" / "PPT_ORG").mkdir()
    return project_path


@pytest.fixture
def browser(app: QApplication, project: Path) -> Iterator[FolderBrowser]:
    """Work를 보고 있는 화면을 제공한다."""
    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget._navigate_to(project / "Work")
    widget.show()
    yield widget
    widget.stop_thumbnail_loading()
    widget.close()


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(10)
        waited_ms += 10
    return condition()


def _items(browser: FolderBrowser) -> list[str]:
    """목록의 항목 이름을 반환한다."""
    folder_list = browser.findChild(QListWidget)
    return [folder_list.item(index).text() for index in range(folder_list.count())]


def _center(browser: FolderBrowser, name: str) -> QPoint:
    """목록에서 항목의 가운데 위치를 반환한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    return folder_list.visualItemRect(item).center()


def _internal_event(paths: list[Path], position: QPoint = QPoint(0, 0)) -> MagicMock:
    """프로그램 안에서 끈 드래그 이벤트를 흉내 낸다."""
    event = MagicMock()
    event.source.return_value = object()
    event.mimeData.return_value = build_file_mime_data(paths)
    event.position.return_value.toPoint.return_value = position
    return event


def _send_mouse(
    widget: QWidget,
    event_type: QEvent.Type,
    position: QPoint,
    buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    """버튼 상태를 지정한 마우스 이벤트를 위젯에 보낸다."""
    button = (
        Qt.MouseButton.NoButton
        if event_type == QEvent.Type.MouseMove
        else Qt.MouseButton.LeftButton
    )
    event = QMouseEvent(
        event_type,
        QPointF(position),
        QPointF(widget.mapToGlobal(position)),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _drag_with_mouse(widget: QWidget, start: QPoint) -> None:
    """위젯 위에서 눌러 끌었다가 놓는 동작을 보낸다."""
    _send_mouse(widget, QEvent.Type.MouseButtonPress, start)
    for step in (15, 30, 35):
        _send_mouse(widget, QEvent.Type.MouseMove, start + QPoint(step, step))
    _send_mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        start + QPoint(30, 30),
        Qt.MouseButton.NoButton,
    )


def test_work_shows_all_file_types_but_document_does_not(
    browser: FolderBrowser, project: Path
) -> None:
    """Work에서는 모든 파일이 보이고, Document에서는 사진과 동영상만 보이는지 확인한다."""
    assert "b.psd" in _items(browser)

    browser._navigate_to(project / "Reference")
    assert "b.psd" in _items(browser)

    browser._navigate_to(project / "Document")
    assert "메모.txt" not in _items(browser)
    assert "c.png" in _items(browser)


def test_plain_file_keeps_file_icon_and_no_thumbnail_job(
    browser: FolderBrowser, project: Path
) -> None:
    """일반 파일은 썸네일을 읽지 않고 일반 아이콘을 유지하는지 확인한다."""
    with patch("ui.folder_browser.ThumbnailLoader") as loader:
        browser._navigate_to(project / "Work" / "보관")
        _write_files(project / "Work" / "보관", "only.psd")
        browser._refresh()

    loader.assert_not_called()
    item = browser.findChild(QListWidget).findItems(
        "only.psd", Qt.MatchFlag.MatchExactly
    )[0]
    assert item.icon().cacheKey() == browser._file_icon.cacheKey()


def test_drop_file_on_folder_moves_it(browser: FolderBrowser, project: Path) -> None:
    """파일을 폴더 위에 놓으면 옮겨지고 화면과 알림이 바뀌는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    source = project / "Work" / "b.psd"
    position = _center(browser, "시안")

    move = _internal_event([source], position)
    folder_list.dragMoveEvent(move)
    move.accept.assert_called_once()

    folder_list.dropEvent(_internal_event([source], position))

    assert _wait_until(lambda: not source.exists())
    assert (project / "Work" / "시안" / "b.psd").exists()
    assert "b.psd" not in _items(browser)
    assert browser.findChild(_Toast).text() == "1개를 옮겼습니다"


def test_moving_is_deferred_until_drag_ends(
    browser: FolderBrowser, project: Path
) -> None:
    """끌기가 끝난 뒤 옮기도록, 놓은 직후에는 아직 옮기지 않는지 확인한다."""
    source = project / "Work" / "b.psd"

    browser.findChild(QListWidget).dropEvent(
        _internal_event([source], _center(browser, "시안"))
    )

    assert source.exists()
    assert _wait_until(lambda: not source.exists())


def test_multiple_files_move_together_with_counts(
    browser: FolderBrowser, project: Path
) -> None:
    """여러 항목을 옮기고, 이미 있는 것은 남기며 함께 알리는지 확인한다."""
    _write_files(project / "Work" / "시안", "a.png")
    sources = [project / "Work" / "a.png", project / "Work" / "b.psd"]

    browser.findChild(QListWidget).dropEvent(
        _internal_event(sources, _center(browser, "시안"))
    )

    assert _wait_until(lambda: not sources[1].exists())
    assert sources[0].exists()
    assert browser.findChild(_Toast).text() == "1개를 옮겼습니다. 1개는 이미 있습니다"


def test_only_existing_name_shows_message(
    browser: FolderBrowser, project: Path
) -> None:
    """모두 이미 있으면 그 내용을 알리는지 확인한다."""
    _write_files(project / "Work" / "시안", "a.png")
    source = project / "Work" / "a.png"

    browser.findChild(QListWidget).dropEvent(
        _internal_event([source], _center(browser, "시안"))
    )

    assert _wait_until(lambda: browser.findChild(_Toast) is not None)
    assert browser.findChild(_Toast).text() == "옮길 폴더에 같은 이름이 이미 있습니다"
    assert source.exists()


def test_drop_folder_on_other_folder_moves_it(
    browser: FolderBrowser, project: Path
) -> None:
    """폴더를 다른 폴더 위에 놓으면 안의 내용과 함께 옮겨지는지 확인한다."""
    source = project / "Work" / "시안"

    browser.findChild(QListWidget).dropEvent(
        _internal_event([source], _center(browser, "보관"))
    )

    assert _wait_until(lambda: not source.exists())
    assert (project / "Work" / "보관" / "시안" / "내부.png").exists()


def test_folder_cannot_be_dropped_on_itself(
    browser: FolderBrowser, project: Path
) -> None:
    """폴더를 자기 위에 놓을 수 없고 강조되지 않는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    source = project / "Work" / "시안"
    item = folder_list.findItems("시안", Qt.MatchFlag.MatchExactly)[0]
    position = _center(browser, "시안")

    move = _internal_event([source], position)
    folder_list.dragMoveEvent(move)
    drop = _internal_event([source], position)
    folder_list.dropEvent(drop)
    QTest.qWait(20)

    move.ignore.assert_called_once()
    drop.ignore.assert_called_once()
    assert item.background().style() == Qt.BrushStyle.NoBrush
    assert source.is_dir()


def test_internal_drop_on_file_or_empty_space_is_ignored(
    browser: FolderBrowser, project: Path
) -> None:
    """파일 위와 빈 공간에는 놓을 수 없는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    source = project / "Work" / "b.psd"

    for position in (_center(browser, "a.png"), QPoint(780, 480)):
        drop = _internal_event([source], position)
        folder_list.dropEvent(drop)
        drop.ignore.assert_called_once()
    QTest.qWait(20)

    assert source.exists()


@pytest.mark.parametrize("parts", [(), ("Document",)])
def test_internal_drag_is_not_accepted_outside_targets(
    browser: FolderBrowser, project: Path, parts: tuple[str, ...]
) -> None:
    """프로젝트 맨 위와 Document에서는 프로그램 안의 항목을 받지 않는지 확인한다."""
    folder = project.joinpath(*parts)
    browser._navigate_to(folder)
    folder_list = browser.findChild(QListWidget)
    name = "Work" if not parts else "PPT_ORG"
    path = project / "Document" / "c.png"
    position = _center(browser, name)

    enter = _internal_event([path], position)
    folder_list.dragEnterEvent(enter)
    drop = _internal_event([path], position)
    folder_list.dropEvent(drop)
    QTest.qWait(20)

    enter.ignore.assert_called_once()
    drop.ignore.assert_called_once()
    assert path.exists()


def test_back_button_drop_moves_up_one_level(
    browser: FolderBrowser, project: Path
) -> None:
    """뒤로 버튼에 놓으면 한 단계 위로 옮기고, 보는 위치는 그대로인지 확인한다."""
    folder = project / "Work" / "시안"
    browser._navigate_to(folder)
    source = folder / "내부.png"
    button = browser._back_button

    enter = _internal_event([source])
    button.dragEnterEvent(enter)
    enter.accept.assert_called_once()
    assert button.isDown()

    button.dropEvent(_internal_event([source]))

    assert not button.isDown()
    assert _wait_until(lambda: not source.exists())
    assert (project / "Work" / "내부.png").exists()
    assert browser._current == folder


@pytest.mark.parametrize("parts", [("Work",), ("Reference",), ("Document",), ()])
def test_back_button_refuses_to_leave_work_or_reference(
    browser: FolderBrowser, project: Path, parts: tuple[str, ...]
) -> None:
    """Work, Reference 맨 위와 그 밖에서는 뒤로 버튼에 놓을 수 없는지 확인한다."""
    folder = project.joinpath(*parts)
    browser._navigate_to(folder)
    source = project / "Work" / "a.png"

    enter = _internal_event([source])
    browser._back_button.dragEnterEvent(enter)
    drop = _internal_event([source])
    browser._back_button.dropEvent(drop)
    QTest.qWait(20)

    enter.ignore.assert_called_once()
    drop.ignore.assert_called_once()
    assert source.exists()


def test_back_button_ignores_external_files(
    browser: FolderBrowser, project: Path
) -> None:
    """밖에서 끌어온 파일은 뒤로 버튼이 받지 않는지 확인한다."""
    browser._navigate_to(project / "Work" / "시안")
    event = _internal_event([project / "Work" / "a.png"])
    event.source.return_value = None

    browser._back_button.dragEnterEvent(event)

    event.ignore.assert_called_once()


def test_dragging_folder_icon_starts_drag_of_folder(
    browser: FolderBrowser, project: Path
) -> None:
    """Work 안의 폴더를 끌면 그 폴더로 드래그가 시작되는지 확인한다."""
    viewport = browser.findChild(QListWidget).viewport()

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_with_mouse(viewport, _center(browser, "시안"))

    start_drag.assert_called_once()
    assert start_drag.call_args.args[1] == [project / "Work" / "시안"]
    assert browser._current == project / "Work"


def test_short_press_on_folder_does_not_start_drag(
    browser: FolderBrowser, project: Path
) -> None:
    """폴더를 살짝 움직이기만 하면 드래그 없이 클릭으로 들어가는지 확인한다."""
    viewport = browser.findChild(QListWidget).viewport()
    position = _center(browser, "시안")

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _send_mouse(viewport, QEvent.Type.MouseButtonPress, position)
        _send_mouse(viewport, QEvent.Type.MouseMove, position + QPoint(1, 0))
        _send_mouse(
            viewport, QEvent.Type.MouseButtonRelease, position, Qt.MouseButton.NoButton
        )

    start_drag.assert_not_called()
    assert browser._current == project / "Work" / "시안"


def test_dragging_folder_row_starts_drag_of_folder(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기에서도 폴더 줄을 끌 수 있는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("시안", Qt.MatchFlag.MatchExactly)[0]

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_with_mouse(tree.viewport(), tree.visualItemRect(item).center())

    assert start_drag.call_args.args[1] == [project / "Work" / "시안"]


def test_folder_outside_targets_cannot_be_dragged(
    browser: FolderBrowser, project: Path
) -> None:
    """프로젝트 맨 위와 Document의 폴더는 끌 수 없는지 확인한다."""
    viewport = browser.findChild(QListWidget).viewport()

    for folder, name in ((project, "Work"), (project / "Document", "PPT_ORG")):
        browser._navigate_to(folder)
        with patch("ui.folder_browser.start_file_drag") as start_drag:
            _drag_with_mouse(viewport, _center(browser, name))
        start_drag.assert_not_called()
        browser._navigate_to(project)


def test_clicking_folder_still_enters_it(
    browser: FolderBrowser, project: Path
) -> None:
    """끌 수 있어도 폴더를 클릭하면 들어가는지 확인한다."""
    QTest.mouseClick(
        browser.findChild(QListWidget).viewport(),
        Qt.MouseButton.LeftButton,
        pos=_center(browser, "시안"),
    )

    assert browser._current == project / "Work" / "시안"


def test_detail_view_drop_on_folder_moves_file(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기의 폴더 줄에 놓아도 옮겨지는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("보관", Qt.MatchFlag.MatchExactly)[0]
    source = project / "Work" / "b.psd"

    tree.dropEvent(_internal_event([source], tree.visualItemRect(item).center()))

    assert _wait_until(lambda: not source.exists())
    assert (project / "Work" / "보관" / "b.psd").exists()
