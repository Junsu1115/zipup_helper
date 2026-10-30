"""Pub 폴더 복사와 삭제, 여러 장 선택, 오른쪽 버튼 메뉴 테스트.

임시 폴더만 사용하고, 실제 휴지통 대신 가짜 함수를 사용한다.
"""

import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QTreeWidget

from core.photo_copy import (
    _move_to_trash,
    copy_photos_to_pub,
    delete_from_pub,
    is_inside_pub,
    pub_destination,
)
from ui.folder_browser import (
    DELETE_MENU_TEXT,
    PUB_MENU_TEXT,
    FolderBrowser,
    ViewMode,
)
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.photo_panel import PhotoPanel
from ui.project_selector import ProjectSelector
from ui.toast import _Toast

MODAL_TIMEOUT_MS = 3000


@pytest.fixture
def fake_trash() -> Iterator[MagicMock]:
    """실제 휴지통 대신, 임시 폴더의 파일을 지우고 성공을 반환하는 함수로 바꾼다."""

    def remove(path: Path) -> bool:
        """파일을 지운다."""
        path.unlink()
        return True

    with patch("core.photo_copy._move_to_trash", side_effect=remove) as trash:
        yield trash


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


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("Work", "Pub"),
        ("Work/Image", "Pub/Image"),
        ("Work/Video", "Pub/Video"),
        ("Work/Image/시안", "Pub/Image/시안"),
    ],
)
def test_pub_destination_mirrors_work(
    tmp_path: Path, folder: str, expected: str
) -> None:
    """Work 안의 위치가 같은 위치의 Pub 폴더에 대응하는지 확인한다."""
    assert pub_destination(tmp_path / folder, tmp_path) == tmp_path / expected


@pytest.mark.parametrize(
    "folder",
    [".", "Document", "Document/PPT_Break/media", "Reference", "Pub", "Pub/Image", "Workshop"],
)
def test_pub_destination_is_none_outside_work(tmp_path: Path, folder: str) -> None:
    """Work 밖과 이름만 비슷한 폴더는 복사 대상이 아닌지 확인한다."""
    assert pub_destination(tmp_path / folder, tmp_path) is None


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("Pub", True),
        ("Pub/Image", True),
        ("Pub/Video/시안", True),
        ("Publish", False),
        ("Work/Pub", False),
        (".", False),
    ],
)
def test_is_inside_pub(tmp_path: Path, folder: str, expected: bool) -> None:
    """Pub 폴더와 그 안만 Pub으로 판단하는지 확인한다."""
    assert is_inside_pub(tmp_path / folder, tmp_path) is expected


def test_copy_image_goes_to_pub_image_and_keeps_original(tmp_path: Path) -> None:
    """Work/Image의 파일이 Pub/Image로 복사되고, 원본이 남는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image", "a.png", "b.png")

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 2
    assert _names(tmp_path / "Pub" / "Image") == {"a.png", "b.png"}
    assert all(source.exists() for source in sources)
    assert not (tmp_path / "Pub" / "a.png").exists()


def test_copy_video_goes_to_pub_video(tmp_path: Path) -> None:
    """Work/Video의 파일이 Pub/Video로 복사되는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Video", "a.mp4")

    copy_photos_to_pub(sources, tmp_path)

    assert _names(tmp_path / "Pub" / "Video") == {"a.mp4"}
    assert not (tmp_path / "Pub" / "Image").exists()


def test_copy_from_work_root_goes_to_pub_root(tmp_path: Path) -> None:
    """Work 바로 아래의 파일은 Pub 바로 아래로 복사되는지 확인한다."""
    sources = _write_files(tmp_path / "Work", "a.png")

    copy_photos_to_pub(sources, tmp_path)

    assert _names(tmp_path / "Pub") == {"a.png"}


def test_copy_from_nested_folder_creates_same_folder(tmp_path: Path) -> None:
    """더 깊은 폴더는 Pub에 같은 폴더를 만들어 복사하는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image" / "시안", "a.png")

    copy_photos_to_pub(sources, tmp_path)

    assert _names(tmp_path / "Pub" / "Image" / "시안") == {"a.png"}


def test_copy_refuses_files_outside_work(tmp_path: Path) -> None:
    """Work 밖의 파일은 복사하지 않고 실패로 알리는지 확인한다."""
    sources = _write_files(tmp_path / "Document" / "PPT_Break" / "media", "a.png")

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 0
    assert "Work" in summary.failures[0]
    assert not (tmp_path / "Pub").exists()


def test_copy_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    """같은 이름의 파일은 덮어쓰지 않고 이미 있는 것으로 세는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image", "a.png", "b.png")
    _write_files(tmp_path / "Pub" / "Image", "a.png")
    (tmp_path / "Pub" / "Image" / "a.png").write_text("기존")

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 1
    assert summary.existing_count == 1
    assert (tmp_path / "Pub" / "Image" / "a.png").read_text() == "기존"


def test_same_name_in_pub_root_does_not_block_copy(tmp_path: Path) -> None:
    """이전 방식으로 Pub 바로 아래에 있는 같은 이름은 영향을 주지 않는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image", "a.png")
    _write_files(tmp_path / "Pub", "a.png")

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 1
    assert (tmp_path / "Pub" / "Image" / "a.png").exists()


def test_copy_failure_continues_and_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """한 개가 실패해도 계속 복사하고, 멈춘 파일을 남기지 않는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image", "a.png", "bad.png", "c.png")
    original_copy = shutil.copy2

    def failing_copy(source: Path, target: Path) -> None:
        """bad.png를 복사하다 멈춘 상황을 만든다."""
        if Path(source).name == "bad.png":
            Path(target).write_text("일부")
            raise OSError("디스크 오류")
        original_copy(source, target)

    monkeypatch.setattr("core.photo_copy.shutil.copy2", failing_copy)

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 2
    assert "bad.png" in summary.failures[0]
    assert _names(tmp_path / "Pub" / "Image") == {"a.png", "c.png"}


def test_copy_reports_failure_when_pub_is_a_file(tmp_path: Path) -> None:
    """Pub이라는 파일이 있으면 실패로 알리고 파일을 건드리지 않는지 확인한다."""
    sources = _write_files(tmp_path / "Work" / "Image", "a.png")
    (tmp_path / "Pub").write_text("파일")

    summary = copy_photos_to_pub(sources, tmp_path)

    assert summary.copied_count == 0
    assert len(summary.failures) == 1
    assert (tmp_path / "Pub").read_text() == "파일"


def test_delete_moves_only_pub_files_to_trash(
    tmp_path: Path, fake_trash: MagicMock
) -> None:
    """Pub 안의 파일만 휴지통으로 옮기고, Work의 같은 이름 파일은 남는지 확인한다."""
    original = _write_files(tmp_path / "Work" / "Image", "a.png")[0]
    copied = _write_files(tmp_path / "Pub" / "Image", "a.png")[0]

    summary = delete_from_pub([copied], tmp_path)

    assert summary.deleted_count == 1
    fake_trash.assert_called_once_with(copied)
    assert not copied.exists()
    assert original.exists()


@pytest.mark.parametrize("folder", ["Work/Image", "Document", "Publish"])
def test_delete_refuses_files_outside_pub(
    tmp_path: Path, fake_trash: MagicMock, folder: str
) -> None:
    """Pub 밖의 파일은 휴지통으로 옮기지 않는지 확인한다."""
    outside = _write_files(tmp_path / folder, "a.png")[0]

    summary = delete_from_pub([outside], tmp_path)

    assert summary.deleted_count == 0
    assert len(summary.failures) == 1
    fake_trash.assert_not_called()
    assert outside.exists()


def test_delete_refuses_folder(tmp_path: Path, fake_trash: MagicMock) -> None:
    """폴더는 삭제하지 않는지 확인한다."""
    folder = tmp_path / "Pub" / "Image"
    folder.mkdir(parents=True)

    summary = delete_from_pub([folder], tmp_path)

    assert summary.deleted_count == 0
    fake_trash.assert_not_called()
    assert folder.is_dir()


def test_delete_failure_keeps_file_and_continues(
    tmp_path: Path, fake_trash: MagicMock
) -> None:
    """휴지통 이동에 실패한 파일은 남기고, 나머지는 계속 처리하는지 확인한다."""
    paths = _write_files(tmp_path / "Pub", "bad.png", "ok.png")

    def trash(path: Path) -> bool:
        """bad.png만 실패시킨다."""
        if path.name == "bad.png":
            return False
        path.unlink()
        return True

    fake_trash.side_effect = trash

    summary = delete_from_pub(paths, tmp_path)

    assert summary.deleted_count == 1
    assert "bad.png" in summary.failures[0]
    assert paths[0].exists()
    assert not paths[1].exists()


def test_real_trash_function_returns_false_for_missing_file(tmp_path: Path) -> None:
    """실제 휴지통 함수가 없는 파일에는 실패를 반환하는지 확인한다."""
    assert _move_to_trash(tmp_path / "없는파일.png") is False


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture
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


def _save_image(path: Path) -> Path:
    """단색 사진 파일을 만들어 경로를 반환한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Work/Image에 사진 세 장, Work/Video에 동영상이 있는 프로젝트를 반환한다."""
    project_path = tmp_path / "project"
    for name in ("a.png", "b.png", "c.png"):
        _save_image(project_path / "Work" / "Image" / name)
    _write_files(project_path / "Work" / "Video", "movie.mp4")
    return project_path


@pytest.fixture
def browser(
    app: QApplication,
    project: Path,
    modal_watchdog: None,
    fake_trash: MagicMock,
) -> Iterator[FolderBrowser]:
    """Work/Image를 보고 있는 화면을 제공한다. 실제 휴지통은 쓰지 않는다."""
    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget._navigate_to(project / "Work" / "Image")
    widget.show()
    yield widget
    widget.stop_thumbnail_loading()
    widget.wait_for_photo_copy()
    widget.close()


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(10)
        waited_ms += 10
    return condition()


def _icon_center(browser: FolderBrowser, name: str) -> QPoint:
    """아이콘 목록에서 항목의 가운데 위치를 반환한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    return folder_list.visualItemRect(item).center()


def _click(
    browser: FolderBrowser,
    name: str,
    modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """아이콘 목록의 항목을 누른다."""
    QTest.mouseClick(
        browser.findChild(QListWidget).viewport(),
        Qt.MouseButton.LeftButton,
        modifier,
        _icon_center(browser, name),
    )


def _selected_names(browser: FolderBrowser) -> list[str]:
    """선택된 파일의 이름을 반환한다."""
    return [path.name for path in browser._selected_images]


class _FakeMenu:
    """화면에 뜨지 않고 첫 항목을 고르는 메뉴. 실제 메뉴는 닫힐 때까지 테스트를 멈춘다."""

    shown_texts: list[str] = []

    def __init__(self, _parent: object) -> None:
        """고를 항목을 준비한다."""
        self._action = object()

    def addAction(self, text: str) -> object:
        """항목 문구를 기록하고 항목을 반환한다."""
        _FakeMenu.shown_texts.append(text)
        return self._action

    def exec(self, _position: object) -> object:
        """첫 항목을 고른 것으로 처리한다."""
        return self._action

    def deleteLater(self) -> None:
        """지울 것이 없다."""


def _choose_menu_action(browser: FolderBrowser, name: str) -> list[str]:
    """파일에 오른쪽 버튼 메뉴를 띄워 첫 항목을 고르고, 메뉴 문구 목록을 반환한다."""
    _FakeMenu.shown_texts = []
    with patch("ui.folder_browser.QMenu", _FakeMenu):
        browser._on_list_context_menu(_icon_center(browser, name))
    return _FakeMenu.shown_texts


def test_shift_click_selects_range(browser: FolderBrowser) -> None:
    """Shift로 누르면 범위가 선택되는지 확인한다."""
    _click(browser, "a.png")
    _click(browser, "c.png", Qt.KeyboardModifier.ShiftModifier)

    assert _selected_names(browser) == ["a.png", "b.png", "c.png"]


def test_command_click_toggles_one_image(browser: FolderBrowser) -> None:
    """⌘로 누르면 한 장씩 추가하고 뺄 수 있는지 확인한다."""
    _click(browser, "a.png")
    _click(browser, "c.png", Qt.KeyboardModifier.ControlModifier)
    assert _selected_names(browser) == ["a.png", "c.png"]

    _click(browser, "a.png", Qt.KeyboardModifier.ControlModifier)
    assert _selected_names(browser) == ["c.png"]


def test_selection_is_shared_with_details(browser: FolderBrowser) -> None:
    """아이콘에서 여러 장을 고르면 자세히 표에도 같게 선택되는지 확인한다."""
    _click(browser, "a.png")
    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)

    browser.set_view_mode(ViewMode.DETAILS)

    tree = browser.findChild(QTreeWidget)
    assert sorted(item.text(0) for item in tree.selectedItems()) == ["a.png", "b.png"]


def test_menu_in_work_image_copies_all_selected_to_pub_image(
    browser: FolderBrowser, project: Path
) -> None:
    """Work/Image에서 메뉴를 고르면 선택한 사진이 Pub/Image로 복사되는지 확인한다."""
    _click(browser, "a.png")
    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)

    shown = _choose_menu_action(browser, "a.png")

    assert shown == [PUB_MENU_TEXT]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(project / "Pub" / "Image") == {"a.png", "b.png"}
    assert (project / "Work" / "Image" / "a.png").exists()
    assert browser.findChild(_Toast).text() == "파일 2개를 Pub 폴더로 복사했습니다"


def test_video_is_listed_and_copied_to_pub_video(
    browser: FolderBrowser, project: Path
) -> None:
    """Work/Video의 동영상이 파일 아이콘으로 보이고, Pub/Video로 복사되는지 확인한다."""
    browser._navigate_to(project / "Work" / "Video")
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems("movie.mp4", Qt.MatchFlag.MatchExactly)[0]
    assert _wait_until(lambda: not browser._thumbnail_loaders)
    assert item.icon().cacheKey() == browser._file_icon.cacheKey()

    shown = _choose_menu_action(browser, "movie.mp4")

    assert shown == [PUB_MENU_TEXT]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(project / "Pub" / "Video") == {"movie.mp4"}


def test_menu_on_unselected_image_selects_only_it(
    browser: FolderBrowser, project: Path
) -> None:
    """선택되지 않은 파일에 메뉴를 띄우면 그 파일만 복사되는지 확인한다."""
    _click(browser, "a.png")
    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)

    _choose_menu_action(browser, "c.png")

    assert _selected_names(browser) == ["c.png"]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(project / "Pub" / "Image") == {"c.png"}


def test_existing_files_show_toast_without_overwriting(
    browser: FolderBrowser, project: Path
) -> None:
    """이미 있으면 하단 알림만 띄우고 덮어쓰지 않는지 확인한다."""
    existing = _write_files(project / "Pub" / "Image", "a.png")[0]
    existing.write_text("기존")
    _click(browser, "a.png")

    with patch("ui.folder_browser.warn") as warning:
        _choose_menu_action(browser, "a.png")
        assert _wait_until(lambda: browser._copy_worker is None)
        QTest.qWait(20)

    warning.assert_not_called()
    assert existing.read_text() == "기존"
    assert browser.findChild(_Toast).text() == "이미 Pub 폴더에 있습니다"


def test_mixed_result_shows_both_counts(
    browser: FolderBrowser, project: Path
) -> None:
    """일부만 있으면 복사한 수와 이미 있는 수를 함께 알리는지 확인한다."""
    _write_files(project / "Pub" / "Image", "a.png")
    _click(browser, "a.png")
    _click(browser, "c.png", Qt.KeyboardModifier.ShiftModifier)

    _choose_menu_action(browser, "b.png")

    assert _wait_until(lambda: browser._copy_worker is None)
    assert _wait_until(lambda: browser.findChild(_Toast) is not None)
    assert browser.findChild(_Toast).text() == (
        "파일 2개를 복사했습니다. 1개는 이미 있습니다"
    )


def test_copy_failure_shows_warning(browser: FolderBrowser, project: Path) -> None:
    """복사에 실패하면 경고창으로 알리는지 확인한다."""
    (project / "Pub").write_text("파일")
    _click(browser, "a.png")

    with patch("ui.folder_browser.warn") as warning:
        _choose_menu_action(browser, "a.png")
        assert _wait_until(lambda: browser._copy_worker is None)
        QTest.qWait(20)

    warning.assert_called_once()
    assert "a.png" in warning.call_args.args[2]


@pytest.mark.parametrize(
    "parts",
    [(), ("Reference",), ("Document", "PPT_Break", "media")],
)
def test_no_menu_outside_work_and_pub(
    browser: FolderBrowser, project: Path, parts: tuple[str, ...]
) -> None:
    """Work와 Pub 밖에서는 파일에 메뉴가 뜨지 않는지 확인한다."""
    folder = project.joinpath(*parts)
    _save_image(folder / "x.png")
    browser._navigate_to(folder)

    with patch("ui.folder_browser.QMenu") as menu_class:
        browser._on_list_context_menu(_icon_center(browser, "x.png"))

    menu_class.assert_not_called()


def test_no_menu_on_folder_or_empty_space(
    browser: FolderBrowser, project: Path
) -> None:
    """폴더와 빈 공간에서는 메뉴가 뜨지 않는지 확인한다."""
    (project / "Work" / "Image" / "시안").mkdir()
    browser._refresh()

    with patch("ui.folder_browser.QMenu") as menu_class:
        browser._on_list_context_menu(_icon_center(browser, "시안"))
        browser._on_list_context_menu(QPoint(780, 480))

    menu_class.assert_not_called()


def test_detail_menu_copies_selected(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기의 메뉴로도 복사되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("b.png", Qt.MatchFlag.MatchExactly)[0]

    with patch("ui.folder_browser.QMenu", _FakeMenu):
        browser._on_tree_context_menu(tree.visualItemRect(item).center())

    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(project / "Pub" / "Image") == {"b.png"}


def test_copy_request_during_copy_shows_toast(
    browser: FolderBrowser, project: Path
) -> None:
    """복사 중에 또 요청하면 알림만 띄우고 새로 시작하지 않는지 확인한다."""
    _click(browser, "a.png")
    busy_worker = object()
    browser._copy_worker = busy_worker

    browser._start_photo_copy()

    assert browser._copy_worker is busy_worker
    assert browser.findChild(_Toast).text() == "복사 중입니다"
    assert not (project / "Pub").exists()
    browser._copy_worker = None


@pytest.mark.parametrize("parts", [("Pub",), ("Pub", "Image"), ("Pub", "Video")])
def test_menu_in_pub_deletes_after_confirmation(
    browser: FolderBrowser,
    project: Path,
    fake_trash: MagicMock,
    parts: tuple[str, ...],
) -> None:
    """Pub 안에서는 삭제 메뉴가 뜨고, 확인하면 Pub의 파일만 지워지는지 확인한다."""
    folder = project.joinpath(*parts)
    copied = _save_image(folder / "a.png")
    browser._navigate_to(folder)
    _click(browser, "a.png")

    with patch("ui.folder_browser.confirm", return_value=True) as confirm_mock:
        shown = _choose_menu_action(browser, "a.png")

    assert shown == [DELETE_MENU_TEXT]
    assert "1개" in confirm_mock.call_args.args[2]
    assert "Pub 폴더에서만 삭제됩니다" in confirm_mock.call_args.args[2]
    fake_trash.assert_called_once_with(copied)
    assert not copied.exists()
    assert (project / "Work" / "Image" / "a.png").exists()
    assert browser.findChild(_Toast).text() == "파일 1개를 삭제했습니다"
    folder_list = browser.findChild(QListWidget)
    assert not folder_list.findItems("a.png", Qt.MatchFlag.MatchExactly)
    assert _selected_names(browser) == []


def test_answering_no_keeps_pub_file(
    browser: FolderBrowser, project: Path, fake_trash: MagicMock
) -> None:
    """삭제 확인에서 아니오를 누르면 지우지 않는지 확인한다."""
    copied = _save_image(project / "Pub" / "Image" / "a.png")
    browser._navigate_to(project / "Pub" / "Image")

    with patch("ui.folder_browser.confirm", return_value=False):
        _choose_menu_action(browser, "a.png")

    fake_trash.assert_not_called()
    assert copied.exists()
    assert browser.findChild(_Toast) is None


def test_delete_all_selected_in_pub(
    browser: FolderBrowser, project: Path
) -> None:
    """여러 개를 선택하면 모두 지우고 개수를 알리는지 확인한다."""
    for name in ("a.png", "b.png", "c.png"):
        _save_image(project / "Pub" / "Image" / name)
    browser._navigate_to(project / "Pub" / "Image")
    _click(browser, "a.png")
    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)

    with patch("ui.folder_browser.confirm", return_value=True) as confirm_mock:
        _choose_menu_action(browser, "a.png")

    assert "2개" in confirm_mock.call_args.args[2]
    assert _names(project / "Pub" / "Image") == {"c.png"}
    assert browser.findChild(_Toast).text() == "파일 2개를 삭제했습니다"


def test_delete_failure_shows_warning(
    browser: FolderBrowser, project: Path, fake_trash: MagicMock
) -> None:
    """휴지통으로 옮기지 못하면 경고하고 파일을 남기는지 확인한다."""
    copied = _save_image(project / "Pub" / "a.png")
    browser._navigate_to(project / "Pub")
    fake_trash.side_effect = None
    fake_trash.return_value = False

    with (
        patch("ui.folder_browser.confirm", return_value=True),
        patch("ui.folder_browser.warn") as warning,
    ):
        _choose_menu_action(browser, "a.png")

    warning.assert_called_once()
    assert copied.exists()
    assert browser.findChild(_Toast) is None


def test_main_window_multi_selection_updates_panel(
    app: QApplication, project: Path, tmp_path: Path
) -> None:
    """여러 개를 고르면 오른쪽 칸에 개수가 나오고, 버튼이 꺼지는지 확인한다."""
    root = tmp_path / "root"
    root.mkdir()
    project.rename(root / "test")
    with patch("ui.main_window.ProjectSelector", lambda: ProjectSelector(root)):
        window = MainWindow()
    window.show()
    browser = window.findChild(FolderBrowser)
    panel = window.findChild(PhotoPanel)
    button = panel._app_buttons.buttons()[0]
    browser._navigate_to(root / "test" / "Work" / "Image")

    _click(browser, "a.png")
    assert panel._name_label.text() == "a.png"
    assert button.isEnabled()

    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)
    assert panel._preview_label.text() == "파일 2개 선택됨"
    assert panel._name_label.text() == ""
    assert not button.isEnabled()

    _click(browser, "b.png", Qt.KeyboardModifier.ControlModifier)
    assert panel._name_label.text() == "a.png"
    assert button.isEnabled()

    browser.stop_thumbnail_loading()
    panel.stop_preview_loading()
    window.close()
