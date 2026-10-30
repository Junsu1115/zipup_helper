"""폴더 아이콘 화면 테스트. 화면 없이 임시 폴더에서 실제로 클릭해 확인한다."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QListWidget,
    QPushButton,
)

from ui.folder_browser import ICON_SIZE, FolderBrowser
from ui.folder_watcher import DebouncedFolderWatcher
from ui.main_window import MainWindow
from ui.project_selector import ProjectSelector


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Work/Image 구조를 가진 임시 프로젝트를 만들어 반환한다."""
    project_path = tmp_path / "프로젝트"
    (project_path / "Work" / "Image").mkdir(parents=True)
    (project_path / "Document").mkdir()
    return project_path


@pytest.fixture
def browser(app: QApplication, project: Path) -> Iterator[FolderBrowser]:
    """프로젝트를 표시 중인 화면을 제공한다."""
    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget.show()
    yield widget
    widget.close()


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(20)
        waited_ms += 20
    return condition()


def _folders(browser: FolderBrowser) -> list[str]:
    """화면에 표시된 폴더 이름을 반환한다."""
    folder_list = browser.findChild(QListWidget)
    return [folder_list.item(index).text() for index in range(folder_list.count())]


def _click_folder(browser: FolderBrowser, name: str) -> None:
    """이름이 같은 폴더 아이콘을 마우스로 한 번 누른다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    QTest.mouseClick(
        folder_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=folder_list.visualItemRect(item).center(),
    )


def _back_button(browser: FolderBrowser) -> QPushButton:
    """뒤로 가기 버튼을 반환한다."""
    return browser.findChild(QPushButton)


def _path_text(browser: FolderBrowser) -> str:
    """현재 경로 문구를 반환한다."""
    return browser._path_label.text()


def test_shows_only_visible_folders_with_big_icons(
    browser: FolderBrowser, project: Path
) -> None:
    """파일과 숨김 폴더를 빼고, 폴더를 큰 아이콘으로 보여주는지 확인한다."""
    (project / "file.txt").touch()
    (project / ".hidden").mkdir()

    browser.set_project(project)

    assert _folders(browser) == ["Document", "Work"]
    folder_list = browser.findChild(QListWidget)
    assert folder_list.iconSize() == ICON_SIZE
    assert not folder_list.item(0).icon().isNull()
    assert folder_list.viewMode() == QListWidget.ViewMode.IconMode


def test_click_enters_folder(browser: FolderBrowser) -> None:
    """아이콘을 한 번 누르면 그 폴더로 들어가는지 확인한다."""
    _click_folder(browser, "Work")

    assert _folders(browser) == ["Image"]
    assert _path_text(browser) == "프로젝트 / Work"
    assert _back_button(browser).isEnabled()


def test_back_returns_and_stops_at_project_top(browser: FolderBrowser) -> None:
    """뒤로 가기로 올라가고, 프로젝트 맨 위에서는 버튼이 꺼지는지 확인한다."""
    assert not _back_button(browser).isEnabled()

    _click_folder(browser, "Work")
    _click_folder(browser, "Image")
    assert _path_text(browser) == "프로젝트 / Work / Image"

    _back_button(browser).click()
    assert _path_text(browser) == "프로젝트 / Work"

    _back_button(browser).click()
    assert _path_text(browser) == "프로젝트"
    assert _folders(browser) == ["Document", "Work"]
    assert not _back_button(browser).isEnabled()


def test_empty_folder_shows_message(browser: FolderBrowser) -> None:
    """빈 폴더에서는 안내 문구가 보이고 아이콘 목록이 숨겨지는지 확인한다."""
    _click_folder(browser, "Document")

    assert browser.findChild(QListWidget).isHidden()
    assert browser._empty_label.isVisible()
    assert browser._empty_label.text() == "폴더가 없습니다"


def test_set_project_shows_top_of_new_project(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """하위 폴더를 보다가 프로젝트를 바꾸면 새 프로젝트 맨 위를 보여주는지 확인한다."""
    other = tmp_path / "다른프로젝트"
    (other / "Pub").mkdir(parents=True)
    _click_folder(browser, "Work")

    browser.set_project(other)

    assert _folders(browser) == ["Pub"]
    assert _path_text(browser) == "다른프로젝트"
    assert not _back_button(browser).isEnabled()


def test_set_project_none_clears_screen(browser: FolderBrowser) -> None:
    """프로젝트가 없으면 빈 화면이 되는지 확인한다."""
    browser.set_project(None)

    assert _folders(browser) == []
    assert _path_text(browser) == ""
    assert not _back_button(browser).isEnabled()
    assert browser._empty_label.isVisible()


def test_external_change_updates_current_folder(
    browser: FolderBrowser, project: Path
) -> None:
    """보고 있는 폴더가 밖에서 바뀌면 아이콘이 바뀌는지 확인한다."""
    _click_folder(browser, "Work")

    (project / "Work" / "Video").mkdir()
    assert _wait_until(lambda: _folders(browser) == ["Image", "Video"])

    (project / "Work" / "Image").rmdir()
    assert _wait_until(lambda: _folders(browser) == ["Video"])
    assert _path_text(browser) == "프로젝트 / Work"


def test_deleted_current_folder_moves_to_nearest_parent(
    browser: FolderBrowser, project: Path
) -> None:
    """보고 있는 폴더가 지워지면 가장 가까운 상위 폴더로 가는지 확인한다."""
    _click_folder(browser, "Work")
    _click_folder(browser, "Image")

    (project / "Work" / "Image").rmdir()

    assert _wait_until(lambda: _path_text(browser) == "프로젝트 / Work")
    assert _folders(browser) == []


def test_moved_parent_folder_moves_to_nearest_parent(
    browser: FolderBrowser, project: Path, tmp_path: Path
) -> None:
    """중간 폴더가 밖으로 옮겨져도 남아 있는 상위 폴더로 가는지 확인한다.

    Finder에서 삭제하면 휴지통으로 옮겨지는 것과 같은 상황이다.
    """
    _click_folder(browser, "Work")
    _click_folder(browser, "Image")

    (project / "Work").rename(tmp_path / "휴지통으로_옮김")

    assert _wait_until(lambda: _path_text(browser) == "프로젝트")
    assert _folders(browser) == ["Document"]


def test_deleted_project_clears_screen(
    browser: FolderBrowser, project: Path, tmp_path: Path
) -> None:
    """프로젝트 자체가 사라지면 경고 없이 빈 화면이 되는지 확인한다."""
    _click_folder(browser, "Work")

    with patch("ui.folder_browser.warn") as warning:
        project.rename(tmp_path / "옮긴프로젝트")
        assert _wait_until(lambda: _path_text(browser) == "")
        QTest.qWait(50)

    warning.assert_not_called()
    assert _folders(browser) == []


def test_read_error_warns_and_stays(
    browser: FolderBrowser,
) -> None:
    """폴더를 읽지 못하면 경고를 띄우고 이전 폴더에 머무는지 확인한다."""
    with (
        patch("ui.folder_browser.warn") as warning,
        patch(
            "ui.folder_browser.list_project_folders",
            side_effect=PermissionError("권한 없음"),
        ),
    ):
        _click_folder(browser, "Work")
        QApplication.processEvents()

    warning.assert_called_once()
    assert _path_text(browser) == "프로젝트"
    assert _folders(browser) == ["Document", "Work"]


def test_unreadable_project_shows_empty_screen(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """고른 프로젝트를 읽지 못하면 경고하고 이전 프로젝트를 남기지 않는지 확인한다."""
    with (
        patch("ui.folder_browser.warn") as warning,
        patch(
            "ui.folder_browser.list_project_folders",
            side_effect=PermissionError("권한 없음"),
        ),
    ):
        browser.set_project(tmp_path / "읽기실패")
        QApplication.processEvents()

    warning.assert_called_once()
    assert _folders(browser) == []
    assert _path_text(browser) == ""


def test_watcher_groups_repeated_changes(app: QApplication) -> None:
    """변경 알림이 연달아 와도 changed를 한 번만 보내는지 확인한다."""
    watcher = DebouncedFolderWatcher()
    received: list[bool] = []
    watcher.changed.connect(lambda: received.append(True))

    for _ in range(5):
        watcher._on_directory_changed("경로")
    QTest.qWait(400)

    assert received == [True]


def test_watcher_replaces_watched_paths(app: QApplication, tmp_path: Path) -> None:
    """감시 경로를 바꾸면 이전 폴더의 변화는 알리지 않는지 확인한다."""
    old = tmp_path / "이전"
    new = tmp_path / "새폴더"
    old.mkdir()
    new.mkdir()
    watcher = DebouncedFolderWatcher()
    received: list[bool] = []
    watcher.changed.connect(lambda: received.append(True))
    watcher.set_paths([old])
    watcher.set_paths([new])

    (old / "하위").mkdir()
    QTest.qWait(500)
    assert received == []

    (new / "하위").mkdir()
    assert _wait_until(lambda: received == [True])


@pytest.fixture
def main_window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """실제 프로젝트 폴더 대신 임시 폴더를 쓰는 메인 창을 제공한다."""
    for name in ("가", "나"):
        (tmp_path / "root" / name / f"{name}_하위").mkdir(parents=True)
    with patch(
        "ui.main_window.ProjectSelector",
        lambda: ProjectSelector(tmp_path / "root"),
    ):
        window = MainWindow()
    window.show()
    yield window
    window.close()


def test_main_window_shows_first_project(main_window: MainWindow) -> None:
    """프로그램을 켜면 첫 프로젝트의 폴더가 보이는지 확인한다."""
    assert _folders(main_window.findChild(FolderBrowser)) == ["가_하위"]


def test_main_window_combo_change_updates_icons(main_window: MainWindow) -> None:
    """콤보박스를 바꾸면 아이콘 화면이 새 프로젝트로 바뀌는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)

    main_window.findChild(QComboBox).setCurrentText("나")

    assert _folders(browser) == ["나_하위"]
    assert _path_text(browser) == "나"


def test_main_window_keeps_subfolder_when_other_project_changes(
    main_window: MainWindow, tmp_path: Path
) -> None:
    """다른 프로젝트가 지워져도 보고 있던 하위 폴더에 머무는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    _click_folder(browser, "가_하위")

    (tmp_path / "root" / "나" / "나_하위").rmdir()
    (tmp_path / "root" / "나").rmdir()

    combo_box = main_window.findChild(QComboBox)
    assert _wait_until(lambda: combo_box.count() == 1)
    assert _path_text(browser) == "가 / 가_하위"
