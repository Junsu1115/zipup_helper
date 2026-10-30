"""폴더 아이콘 화면 테스트. 화면 없이 임시 폴더에서 실제로 클릭해 확인한다."""

import os
import unicodedata
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QListWidget,
    QPushButton,
    QTreeWidget,
)

from ui.folder_browser import (
    ICON_SIZE,
    SMALL_GRID_SIZE,
    SMALL_ICON_SIZE,
    FolderBrowser,
    ViewMode,
)
from ui.folder_watcher import DebouncedFolderWatcher
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.project_selector import ProjectSelector
from ui.thumbnail_loader import THUMBNAIL_SIZE, load_thumbnail
from ui.toast import _Toast


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


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
    widget.stop_thumbnail_loading()
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
    assert browser._empty_label.text() == "폴더나 사진이 없습니다"


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


def _click_add_subfolders(browser: FolderBrowser, answer: bool) -> None:
    """하위 폴더 추가 버튼을 누르고 확인창에 응답한다."""
    with patch("ui.folder_browser.confirm", return_value=answer):
        browser._add_subfolders_button.click()


def test_add_subfolders_button_only_enabled_at_project_top(
    browser: FolderBrowser, project: Path
) -> None:
    """맨 위에서만 버튼이 켜지고, 하위 폴더에서는 눌러도 동작하지 않는지 확인한다."""
    button = browser._add_subfolders_button
    assert button.text() == "이 위치에 하위 폴더 추가"
    assert button.isEnabled()

    _click_folder(browser, "Work")
    assert not button.isEnabled()

    with patch("ui.folder_browser.confirm") as confirm_mock:
        button.click()
    confirm_mock.assert_not_called()
    assert _folders(browser) == ["Image"]
    assert not (project / "Work" / "Document").exists()

    _back_button(browser).click()
    assert button.isEnabled()


def test_add_subfolders_button_disabled_without_project(
    browser: FolderBrowser,
) -> None:
    """프로젝트가 없으면 버튼이 꺼지는지 확인한다."""
    browser.set_project(None)

    assert not browser._add_subfolders_button.isEnabled()


def test_answering_no_adds_nothing(browser: FolderBrowser, project: Path) -> None:
    """확인창에서 아니오를 누르면 아무것도 추가하지 않는지 확인한다."""
    _click_add_subfolders(browser, answer=False)

    assert {entry.name for entry in project.iterdir()} == {"Document", "Work"}
    assert browser.findChild(_Toast) is None


def test_answering_yes_adds_folders_and_shows_toast(
    browser: FolderBrowser, project: Path
) -> None:
    """예를 누르면 없는 폴더를 추가하고, 아이콘 갱신과 알림을 표시하는지 확인한다."""
    _click_add_subfolders(browser, answer=True)

    assert _folders(browser) == ["Document", "Pub", "Reference", "Work"]
    assert (project / "Document" / "PPT_ORG").is_dir()
    assert (project / "Pub" / "Video").is_dir()
    assert (project / "Work" / "Image").is_dir()
    assert browser.findChild(_Toast).text() == "하위 폴더가 추가되었습니다"


def test_nothing_to_add_shows_toast(browser: FolderBrowser) -> None:
    """추가할 폴더가 없으면 그 내용을 알리는지 확인한다."""
    _click_add_subfolders(browser, answer=True)
    _click_add_subfolders(browser, answer=True)

    assert browser.findChild(_Toast).text() == "이미 모든 하위 폴더가 있습니다"


def test_add_failure_warns_and_refreshes(
    browser: FolderBrowser, project: Path
) -> None:
    """같은 이름의 파일 때문에 실패하면 경고하고, 만들어진 폴더를 표시하는지 확인한다."""
    (project / "Reference").write_text("파일")

    with patch("ui.folder_browser.warn") as warning:
        _click_add_subfolders(browser, answer=True)

    warning.assert_called_once()
    assert (project / "Reference").is_file()
    assert _folders(browser) == ["Document", "Work"]
    assert browser.findChild(_Toast) is None


def _save_image(path: Path, width: int = 400, height: int = 200) -> Path:
    """단색 사진 파일을 만들어 경로를 반환한다."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    # Qt는 저장할 때 한글 이름을 자모 분리 형태로 바꾸므로, 영문 이름으로 저장한 뒤 바꾼다.
    temporary_path = path.with_name(f"temporary{path.suffix}")
    assert image.save(str(temporary_path))
    temporary_path.rename(path)
    return path


def _item(browser: FolderBrowser, name: str):
    """이름이 같은 목록 항목을 반환한다."""
    folder_list = browser.findChild(QListWidget)
    return folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]


def _has_thumbnail(browser: FolderBrowser, name: str) -> bool:
    """항목의 아이콘이 일반 파일 아이콘에서 썸네일로 바뀌었는지 반환한다."""
    return _item(browser, name).icon().cacheKey() != browser._file_icon.cacheKey()


def test_folders_and_images_are_mixed_by_name(
    browser: FolderBrowser, project: Path
) -> None:
    """폴더와 사진이 섞여 이름순으로 나오고, 다른 파일과 숨김 파일은 빠지는지 확인한다."""
    _save_image(project / "b.png")
    _save_image(project / "a.jpg")
    _save_image(project / ".hidden.png")
    (project / "메모.txt").write_text("메모")
    (project / "회사.pptx").write_bytes(b"x")

    browser.set_project(project)

    assert _folders(browser) == ["a.jpg", "b.png", "Document", "Work"]


def test_thumbnail_replaces_file_icon_with_square(
    browser: FolderBrowser, project: Path
) -> None:
    """파일 아이콘이 썸네일로 바뀌고, 비율과 상관없이 아이콘 칸이 같은지 확인한다."""
    _save_image(project / "사진.png", 400, 200)

    browser.set_project(project)

    assert _wait_until(lambda: _has_thumbnail(browser, "사진.png"))
    assert _item(browser, "사진.png").icon().actualSize(THUMBNAIL_SIZE) == THUMBNAIL_SIZE


def test_decomposed_korean_file_name_gets_thumbnail(
    browser: FolderBrowser, project: Path
) -> None:
    """macOS식 자모 분리 한글 이름의 사진도 썸네일이 나오는지 확인한다."""
    name = unicodedata.normalize("NFD", "사진.png")
    _save_image(project / name)

    browser.set_project(project)

    assert _wait_until(lambda: _has_thumbnail(browser, name))


def test_folder_with_only_images_shows_list(
    browser: FolderBrowser, project: Path
) -> None:
    """사진만 있는 폴더에서 안내 문구 대신 사진이 보이는지 확인한다."""
    _save_image(project / "Document" / "사진.png")

    _click_folder(browser, "Document")

    assert _folders(browser) == ["사진.png"]
    assert browser._empty_label.isHidden()
    assert browser.findChild(QListWidget).isVisible()


def test_clicking_image_does_not_navigate(
    browser: FolderBrowser, project: Path
) -> None:
    """사진을 눌러도 폴더가 바뀌거나 경고가 뜨지 않는지 확인한다."""
    _save_image(project / "사진.png")
    browser.set_project(project)

    with patch("ui.folder_browser.warn") as warning:
        _click_folder(browser, "사진.png")
        QApplication.processEvents()

    warning.assert_not_called()
    assert _path_text(browser) == "프로젝트"
    assert "사진.png" in _folders(browser)


def test_unreadable_images_keep_file_icon(
    browser: FolderBrowser, project: Path
) -> None:
    """읽지 못하는 사진은 일반 파일 아이콘으로 남고, 다른 사진은 표시되는지 확인한다."""
    (project / "손상.png").write_bytes(b"not an image")
    (project / "도형.emf").write_bytes(b"not an image")
    _save_image(project / "정상.png")

    with patch("ui.folder_browser.warn") as warning:
        browser.set_project(project)
        assert _wait_until(lambda: _has_thumbnail(browser, "정상.png"))
        QTest.qWait(50)

    warning.assert_not_called()
    assert not _has_thumbnail(browser, "손상.png")
    assert not _has_thumbnail(browser, "도형.emf")


def test_thumbnail_of_previous_folder_is_ignored(
    browser: FolderBrowser, project: Path
) -> None:
    """이전 폴더의 썸네일이 같은 이름의 사진에 적용되지 않는지 확인한다."""
    _save_image(project / "Work" / "사진.png")
    _click_folder(browser, "Work")
    assert _wait_until(lambda: _has_thumbnail(browser, "사진.png"))
    icon_key = _item(browser, "사진.png").icon().cacheKey()

    image = QImage(10, 10, QImage.Format.Format_RGB32)
    browser._on_thumbnail_ready(str(project), "사진.png", image)

    assert _item(browser, "사진.png").icon().cacheKey() == icon_key


def test_moving_folders_during_loading_stays_consistent(
    browser: FolderBrowser, project: Path
) -> None:
    """썸네일 생성 중에 이동해도, 지금 폴더의 사진만 표시되는지 확인한다."""
    for index in range(30):
        _save_image(project / f"{index:02}.png", 800, 800)
    _save_image(project / "Work" / "work.png")
    browser.set_project(project)

    _click_folder(browser, "Work")

    assert _folders(browser) == ["Image", "work.png"]
    assert _wait_until(lambda: _has_thumbnail(browser, "work.png"))
    assert _wait_until(lambda: not browser._thumbnail_loaders)


def test_external_image_change_updates_list(
    browser: FolderBrowser, project: Path
) -> None:
    """사진이 밖에서 추가되거나 지워지면 목록이 바뀌는지 확인한다."""
    _save_image(project / "사진.png")
    assert _wait_until(lambda: "사진.png" in _folders(browser))
    assert _wait_until(lambda: _has_thumbnail(browser, "사진.png"))

    (project / "사진.png").unlink()

    assert _wait_until(lambda: _folders(browser) == ["Document", "Work"])


def test_refresh_with_same_entries_keeps_items(
    browser: FolderBrowser, project: Path
) -> None:
    """목록이 그대로이면 다시 그리지 않고 스레드도 새로 만들지 않는지 확인한다."""
    _save_image(project / "사진.png")
    browser.set_project(project)
    assert _wait_until(lambda: _has_thumbnail(browser, "사진.png"))
    assert _wait_until(lambda: not browser._thumbnail_loaders)
    icon_key = _item(browser, "사진.png").icon().cacheKey()

    browser._refresh()

    assert not browser._thumbnail_loaders
    assert _item(browser, "사진.png").icon().cacheKey() == icon_key


def _filled_rect(image: QImage) -> tuple[int, int, int, int]:
    """이미지에서 불투명한 영역의 왼쪽, 위, 오른쪽, 아래 좌표를 반환한다."""
    points = [
        (x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 0
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def test_load_thumbnail_centers_large_image_keeping_ratio(
    app: QApplication, tmp_path: Path
) -> None:
    """큰 사진은 비율을 유지해 줄이고 칸 가운데에 놓는지 확인한다."""
    image = load_thumbnail(_save_image(tmp_path / "가로.png", 1000, 500))

    assert image.size() == THUMBNAIL_SIZE
    assert _filled_rect(image) == (0, 48, 191, 143)

    tall = load_thumbnail(_save_image(tmp_path / "세로.png", 500, 1000))
    assert _filled_rect(tall) == (48, 0, 143, 191)


def test_load_thumbnail_keeps_small_image_size(
    app: QApplication, tmp_path: Path
) -> None:
    """작은 사진은 늘리지 않고 칸 가운데에 놓는지 확인한다."""
    image = load_thumbnail(_save_image(tmp_path / "작은.png", 50, 20))

    assert image.size() == THUMBNAIL_SIZE
    assert _filled_rect(image) == (71, 86, 120, 105)


def test_load_thumbnail_applies_exif_rotation(
    app: QApplication, tmp_path: Path
) -> None:
    """사진에 저장된 회전 정보가 반영되는지 확인한다."""
    path = _save_image(tmp_path / "회전.jpg", 400, 200)
    # 90도 회전(Orientation=6)을 뜻하는 EXIF 영역을 JPEG 시작 부분에 넣는다.
    exif = b"Exif\0\0MM\0*\0\0\0\x08\0\x01\x01\x12\0\x03\0\0\0\x01\0\x06\0\0\0\0\0\0"
    segment = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    data = path.read_bytes()
    path.write_bytes(data[:2] + segment + data[2:])

    left, top, right, bottom = _filled_rect(load_thumbnail(path))

    assert bottom - top > right - left


def test_load_thumbnail_returns_null_for_broken_file(
    app: QApplication, tmp_path: Path
) -> None:
    """사진이 아닌 파일은 비어 있는 결과를 반환하는지 확인한다."""
    broken = tmp_path / "손상.png"
    broken.write_bytes(b"not an image")

    assert load_thumbnail(broken).isNull()


def _view_buttons(browser: FolderBrowser) -> list[QPushButton]:
    """보기 버튼들을 반환한다. 선택할 수 있는 버튼은 보기 버튼뿐이다."""
    return [
        button
        for button in browser.findChildren(QPushButton)
        if button.isCheckable()
    ]


def _view_button(browser: FolderBrowser, label: str) -> QPushButton:
    """문구가 같은 보기 버튼을 반환한다."""
    return next(
        button for button in _view_buttons(browser) if button.text() == label
    )


def _tree(browser: FolderBrowser) -> QTreeWidget:
    """자세히 보기 표를 반환한다."""
    return browser.findChild(QTreeWidget)


def _tree_column(browser: FolderBrowser, column: int = 0) -> list[str]:
    """자세히 보기 표의 한 열을 위에서부터 차례로 반환한다."""
    tree = _tree(browser)
    return [
        tree.topLevelItem(index).text(column)
        for index in range(tree.topLevelItemCount())
    ]


def _click_tree_row(browser: FolderBrowser, name: str) -> None:
    """자세히 보기 표에서 이름이 같은 줄을 마우스로 한 번 누른다."""
    tree = _tree(browser)
    item = tree.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    QTest.mouseClick(
        tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=tree.visualItemRect(item).center(),
    )


def test_view_buttons_start_with_large_icons(browser: FolderBrowser) -> None:
    """보기 버튼 세 개가 있고, 처음에는 큰 아이콘 보기인지 확인한다."""
    labels = [button.text() for button in _view_buttons(browser)]

    assert labels == ["큰 아이콘", "작은 아이콘", "자세히"]
    for button in _view_buttons(browser):
        assert button.height() == _back_button(browser).height()
        assert button.font() == _back_button(browser).font()
    assert _view_button(browser, "큰 아이콘").isChecked()
    assert browser.findChild(QListWidget).isVisible()
    assert _tree(browser).isHidden()
    assert browser.findChild(QListWidget).iconSize() == ICON_SIZE


def test_small_icons_button_changes_icon_size(browser: FolderBrowser) -> None:
    """작은 아이콘을 누르면 아이콘과 칸이 작아지고, 버튼 선택이 옮겨지는지 확인한다."""
    _view_button(browser, "작은 아이콘").click()

    folder_list = browser.findChild(QListWidget)
    assert folder_list.iconSize() == SMALL_ICON_SIZE
    assert folder_list.gridSize() == SMALL_GRID_SIZE
    assert folder_list.isVisible()
    assert _view_button(browser, "작은 아이콘").isChecked()
    assert not _view_button(browser, "큰 아이콘").isChecked()


def test_details_button_shows_table(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히를 누르면 표가 보이고, 이름순으로 정보가 나오는지 확인한다."""
    _save_image(project / "사진.png")
    browser.set_project(project)

    _view_button(browser, "자세히").click()

    tree = _tree(browser)
    assert tree.isVisible()
    assert browser.findChild(QListWidget).isHidden()
    headers = [tree.headerItem().text(column) for column in range(4)]
    assert headers == ["이름", "수정일", "크기", "종류"]
    assert _tree_column(browser, 0) == ["Document", "Work", "사진.png"]
    assert _tree_column(browser, 2)[:2] == ["--", "--"]
    assert _tree_column(browser, 3) == ["폴더", "폴더", "PNG 이미지"]
    assert "오" in _tree_column(browser, 1)[2]


def test_returning_from_details_restores_icons(browser: FolderBrowser) -> None:
    """자세히에서 큰 아이콘으로 돌아오면 원래 화면인지 확인한다."""
    _view_button(browser, "자세히").click()
    _view_button(browser, "큰 아이콘").click()

    assert browser.findChild(QListWidget).isVisible()
    assert _tree(browser).isHidden()
    assert browser.findChild(QListWidget).iconSize() == ICON_SIZE


def test_empty_folder_shows_message_in_every_view(browser: FolderBrowser) -> None:
    """빈 폴더에서는 어떤 보기든 안내 문구만 보이는지 확인한다."""
    _click_folder(browser, "Document")

    for label in ("큰 아이콘", "작은 아이콘", "자세히"):
        _view_button(browser, label).click()
        assert browser._empty_label.isVisible()
        assert browser.findChild(QListWidget).isHidden()
        assert _tree(browser).isHidden()


def test_click_in_details_enters_folder_and_back_keeps_view(
    browser: FolderBrowser,
) -> None:
    """자세히 보기에서 한 번 눌러 들어가고, 이동해도 보기가 유지되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)

    _click_tree_row(browser, "Work")

    assert _path_text(browser) == "프로젝트 / Work"
    assert _tree_column(browser) == ["Image"]
    assert _tree(browser).isVisible()

    _back_button(browser).click()

    assert _path_text(browser) == "프로젝트"
    assert _view_button(browser, "자세히").isChecked()


def test_view_is_kept_when_project_changes(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """프로젝트를 바꿔도 선택한 보기가 유지되는지 확인한다."""
    other = tmp_path / "다른프로젝트"
    (other / "Pub").mkdir(parents=True)
    _view_button(browser, "작은 아이콘").click()

    browser.set_project(other)

    assert browser.findChild(QListWidget).iconSize() == SMALL_ICON_SIZE
    assert _view_button(browser, "작은 아이콘").isChecked()


def test_clicking_image_in_details_does_not_navigate(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기에서 사진을 눌러도 이동하지 않는지 확인한다."""
    _save_image(project / "사진.png")
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)

    _click_tree_row(browser, "사진.png")

    assert _path_text(browser) == "프로젝트"


def test_details_sort_uses_real_values(
    browser: FolderBrowser, project: Path
) -> None:
    """크기순 정렬이 문구가 아닌 실제 크기로 이루어지는지 확인한다."""
    (project / "큰.png").write_bytes(b"x" * 2000)
    (project / "작은.png").write_bytes(b"x" * 300)
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)
    tree = _tree(browser)

    tree.sortByColumn(2, Qt.SortOrder.AscendingOrder)
    assert _tree_column(browser)[-2:] == ["작은.png", "큰.png"]

    tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    assert _tree_column(browser)[:2] == ["큰.png", "작은.png"]


def test_details_starts_sorted_by_name_with_indicator(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기가 이름순으로 시작하고, 제목에 정렬 표시가 있는지 확인한다."""
    (project / "A.png").write_bytes(b"x")
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)

    header = _tree(browser).header()
    assert _tree_column(browser) == ["A.png", "Document", "Work"]
    assert header.sortIndicatorSection() == 0
    assert header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder


def test_clicking_name_header_reverses_order(
    browser: FolderBrowser, project: Path
) -> None:
    """이름 제목을 누르면 순서가 반대로 바뀌는지 확인한다."""
    (project / "A.png").write_bytes(b"x")
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)

    header = _tree(browser).header()
    QTest.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(10, header.height() // 2),
    )

    assert _tree_column(browser) == ["Work", "Document", "A.png"]


def test_all_views_use_same_natural_name_order(
    browser: FolderBrowser, project: Path
) -> None:
    """모든 보기가 같은 순서이고, 이름 속 숫자를 크기순으로 정렬하는지 확인한다."""
    for name in ("image10.png", "image2.png", "image1.png"):
        (project / name).write_bytes(b"x")
    (project / "image3").mkdir()

    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)

    expected = [
        "Document",
        "image1.png",
        "image2.png",
        "image3",
        "image10.png",
        "Work",
    ]
    assert _folders(browser) == expected
    assert _tree_column(browser) == expected


def test_opening_other_folder_resets_sort_to_name(
    browser: FolderBrowser, project: Path
) -> None:
    """다른 폴더를 열면 정렬이 이름순으로 돌아오는지 확인한다."""
    (project / "Work" / "큰.png").write_bytes(b"x" * 2000)
    (project / "Work" / "가작은.png").write_bytes(b"x" * 300)
    browser.set_view_mode(ViewMode.DETAILS)
    tree = _tree(browser)
    tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)

    _click_tree_row(browser, "Work")

    assert tree.header().sortIndicatorSection() == 0
    assert tree.header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
    assert _tree_column(browser) == ["Image", "가작은.png", "큰.png"]

    tree.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    _back_button(browser).click()

    assert tree.header().sortIndicatorSection() == 0
    assert _tree_column(browser) == ["Document", "Work"]


def test_refresh_keeps_chosen_sort_in_same_folder(
    browser: FolderBrowser, project: Path
) -> None:
    """같은 폴더가 자동으로 새로 고쳐져도 선택한 정렬이 유지되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = _tree(browser)
    tree.sortByColumn(0, Qt.SortOrder.DescendingOrder)

    (project / "Apple").mkdir()

    assert _wait_until(
        lambda: _tree_column(browser) == ["Work", "Document", "Apple"]
    )
    assert tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


def test_thumbnail_after_sort_stays_on_matching_row(
    browser: FolderBrowser, project: Path
) -> None:
    """정렬을 바꾼 뒤 도착한 썸네일도 이름이 맞는 줄에 적용되는지 확인한다."""
    _save_image(project / "a.png", 800, 800)
    _save_image(project / "z.png", 800, 800)
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)
    _tree(browser).sortByColumn(0, Qt.SortOrder.DescendingOrder)

    assert _wait_until(lambda: _has_thumbnail(browser, "a.png"))
    assert _wait_until(lambda: _has_thumbnail(browser, "z.png"))

    for name in ("a.png", "z.png"):
        row = _tree(browser).findItems(name, Qt.MatchFlag.MatchExactly)[0]
        assert row.icon(0).cacheKey() == _item(browser, name).icon().cacheKey()


def test_thumbnail_applies_to_details_too(
    browser: FolderBrowser, project: Path
) -> None:
    """자세히 보기에도 썸네일이 적용되고, 정렬 후에도 맞는 줄에 있는지 확인한다."""
    _save_image(project / "사진.png")
    browser.set_project(project)
    browser.set_view_mode(ViewMode.DETAILS)

    assert _wait_until(lambda: _has_thumbnail(browser, "사진.png"))

    item = _tree(browser).findItems("사진.png", Qt.MatchFlag.MatchExactly)[0]
    assert item.icon(0).cacheKey() == _item(browser, "사진.png").icon().cacheKey()


def test_external_change_updates_details(
    browser: FolderBrowser, project: Path
) -> None:
    """밖에서 폴더가 바뀌면 자세히 보기에도 반영되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)

    (project / "새폴더").mkdir()

    assert _wait_until(
        lambda: _tree_column(browser) == ["Document", "Work", "새폴더"]
    )


def test_long_path_does_not_widen_browser(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """경로가 아주 길어도 최소 너비가 늘지 않고, text()는 전체를 반환하는지 확인한다."""
    deep = tmp_path / ("아주긴이름" * 20)
    deep.mkdir()
    before = browser.minimumSizeHint().width()

    browser.set_project(deep)

    assert browser.minimumSizeHint().width() == before
    assert _path_text(browser) == "아주긴이름" * 20


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
