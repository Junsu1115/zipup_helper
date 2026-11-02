"""사진 칸과 사진 선택 테스트. 실제 포토샵은 실행하지 않고 가짜 명령만 사용한다."""

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QProcess, QSize, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QTreeWidget

from core.external_apps import EXTERNAL_APPS, AppNotFoundError
from core.prompt_templates import load_image_analysis_template
from ui.folder_browser import FolderBrowser, ViewMode
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.photo_panel import (
    LOADING_TEXT,
    NO_PREVIEW_TEXT,
    NO_SELECTION_TEXT,
    PhotoPanel,
)
from ui.project_selector import ProjectSelector
from ui.thumbnail_loader import load_preview
from ui.toast import _Toast


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


SUCCESS_COMMAND = [sys.executable, "-c", ""]
FAILURE_COMMAND = [sys.executable, "-c", "import sys; sys.exit(1)"]


@pytest.fixture(autouse=True)
def fake_launcher() -> Iterator[MagicMock]:
    """테스트 중에 실제 포토샵이 실행되지 않도록 가짜 실행 방식으로 바꾼다."""
    launcher = MagicMock()
    launcher.opens_detached = False
    with patch("ui.photo_panel.get_app_launcher", return_value=launcher):
        yield launcher


@pytest.fixture(autouse=True)
def fake_open_command(fake_launcher: MagicMock) -> MagicMock:
    """아무 일도 하지 않는 명령을 만드는 가짜 함수를 제공한다."""
    fake_launcher.build_command.return_value = SUCCESS_COMMAND
    return fake_launcher.build_command


@pytest.fixture(autouse=True)
def fake_desktop_services() -> Iterator[MagicMock]:
    """테스트 중에 실제 기본 프로그램이 열리지 않도록 가짜로 바꾼다."""
    with patch("ui.photo_panel.QDesktopServices") as services:
        services.openUrl.return_value = True
        yield services


@pytest.fixture
def panel(app: QApplication) -> Iterator[PhotoPanel]:
    """화면에 표시된 사진 칸을 제공한다."""
    widget = PhotoPanel()
    widget.resize(260, 500)
    widget.show()
    yield widget
    widget.stop_preview_loading()
    widget.close()


def _save_image(path: Path, width: int = 400, height: int = 200) -> Path:
    """단색 사진 파일을 만들어 경로를 반환한다."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    # Qt는 저장할 때 한글 이름을 자모 분리 형태로 바꾸므로, 영문 이름으로 저장한 뒤 바꾼다.
    temporary_path = path.with_name(f"temporary{path.suffix}")
    assert image.save(str(temporary_path))
    temporary_path.rename(path)
    return path


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(10)
        waited_ms += 10
    return condition()


def _app_button(panel: PhotoPanel) -> QPushButton:
    """포토샵 버튼을 반환한다."""
    return panel.findChild(QPushButton)


def _preview_shown(panel: PhotoPanel) -> bool:
    """미리보기 사진이 표시되어 있는지 반환한다."""
    return not panel._preview_label.pixmap().isNull()


def test_no_selection_shows_guidance_and_disables_button(panel: PhotoPanel) -> None:
    """선택한 사진이 없으면 안내 문구가 보이고 버튼이 꺼져 있는지 확인한다."""
    assert panel._preview_label.text() == NO_SELECTION_TEXT
    assert _app_button(panel).text() == "Adobe Photoshop"
    assert not _app_button(panel).isEnabled()


def test_set_image_shows_preview_name_and_info(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """사진을 지정하면 미리보기, 이름, 크기 정보가 표시되는지 확인한다."""
    path = _save_image(tmp_path / "사진.png", 800, 400)

    panel.set_image(path)

    assert panel._name_label.text() == "사진.png"
    assert _app_button(panel).isEnabled()
    assert _wait_until(lambda: _preview_shown(panel))
    assert panel._info_label.text().startswith("800 × 400")
    preview = panel._preview_label.pixmap()
    ratio = preview.width() / preview.height()
    assert ratio == pytest.approx(2, abs=0.05)
    assert preview.deviceIndependentSize().width() <= panel._preview_label.width()


def test_set_none_clears_panel(panel: PhotoPanel, tmp_path: Path) -> None:
    """선택을 해제하면 처음 상태로 돌아가는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png"))
    assert _wait_until(lambda: _preview_shown(panel))

    panel.set_image(None)

    assert panel._preview_label.text() == NO_SELECTION_TEXT
    assert not _preview_shown(panel)
    assert panel._name_label.text() == ""
    assert not _app_button(panel).isEnabled()


def test_unreadable_image_shows_message_and_keeps_button(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """미리보기가 안 되는 파일은 안내를 표시하고, 버튼은 켜 두는지 확인한다."""
    path = tmp_path / "도형.emf"
    path.write_bytes(b"not an image")

    panel.set_image(path)

    assert _wait_until(lambda: panel._preview_label.text() == NO_PREVIEW_TEXT)
    assert _app_button(panel).isEnabled()


def test_preview_of_previous_selection_is_ignored(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """이전에 선택한 사진의 결과가 늦게 도착해도 표시되지 않는지 확인한다."""
    first = _save_image(tmp_path / "첫번째.png")
    second = _save_image(tmp_path / "두번째.png")
    panel.set_image(second)
    assert _wait_until(lambda: _preview_shown(panel))
    shown_key = panel._preview_label.pixmap().cacheKey()

    image = QImage(10, 10, QImage.Format.Format_RGB32)
    panel._on_preview_ready(str(first), image, QSize(10, 10))

    assert panel._preview_label.pixmap().cacheKey() == shown_key
    assert panel._name_label.text() == "두번째.png"


def test_resizing_panel_rescales_preview(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """칸 크기를 바꾸면 미리보기도 맞춰지는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png", 1000, 1000))
    assert _wait_until(lambda: _preview_shown(panel))
    before = panel._preview_label.pixmap().deviceIndependentSize().width()

    panel.resize(500, 700)
    QApplication.processEvents()

    assert panel._preview_label.pixmap().deviceIndependentSize().width() > before


def test_load_preview_shrinks_and_reports_original_size(
    app: QApplication, tmp_path: Path
) -> None:
    """큰 사진은 줄여 읽되, 원본 크기를 함께 알려 주는지 확인한다."""
    path = _save_image(tmp_path / "큰.png", 3200, 1600)

    image, original_size = load_preview(path)

    assert (image.width(), image.height()) == (1600, 800)
    assert (original_size.width(), original_size.height()) == (3200, 1600)


def test_load_preview_reports_rotated_original_size(
    app: QApplication, tmp_path: Path
) -> None:
    """회전 정보가 있는 사진은 회전한 크기를 알려 주는지 확인한다."""
    path = _save_image(tmp_path / "회전.jpg", 400, 200)
    # 90도 회전(Orientation=6)을 뜻하는 EXIF 영역을 JPEG 시작 부분에 넣는다.
    exif = b"Exif\0\0MM\0*\0\0\0\x08\0\x01\x01\x12\0\x03\0\0\0\x01\0\x06\0\0\0\0\0\0"
    segment = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    data = path.read_bytes()
    path.write_bytes(data[:2] + segment + data[2:])

    image, original_size = load_preview(path)

    assert (original_size.width(), original_size.height()) == (200, 400)
    assert image.height() > image.width()


def test_button_runs_command_for_selected_image(
    panel: PhotoPanel, tmp_path: Path, fake_open_command: MagicMock
) -> None:
    """버튼을 누르면 선택한 사진과 포토샵으로 명령을 만들어 실행하는지 확인한다."""
    path = _save_image(tmp_path / "사진.png")
    panel.set_image(path)

    with patch("ui.photo_panel.warn") as warning:
        _app_button(panel).click()
        assert _wait_until(lambda: panel._open_process is None)

    fake_open_command.assert_called_once_with(EXTERNAL_APPS[0], path)
    warning.assert_not_called()


def _click_template_button(panel: PhotoPanel) -> MagicMock:
    """사용 중인 클립보드 대신 가짜 클립보드로 템플릿 버튼을 누르고, 가짜를 반환한다."""
    clipboard = MagicMock()
    with patch("ui.photo_panel.QApplication.clipboard", return_value=clipboard):
        panel._template_button.click()
    return clipboard


def test_template_button_is_below_app_button(panel: PhotoPanel) -> None:
    """템플릿 버튼이 문구에 맞게 포토샵 버튼 바로 아래에 있는지 확인한다."""
    layout = panel.layout()
    app_index = layout.indexOf(_app_button(panel))

    assert panel._template_button.text() == "이미지 분석 템플릿 사용"
    assert layout.indexOf(panel._template_button) == app_index + 1
    assert panel._template_button.y() > _app_button(panel).y()


def test_template_button_copies_whole_template_and_shows_toast(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """사진을 선택한 뒤 누르면 템플릿 전체가 복사되고 하단 알림이 뜨는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png"))

    clipboard = _click_template_button(panel)

    clipboard.setText.assert_called_once_with(load_image_analysis_template())
    assert panel.findChild(_Toast).text() == "클립보드에 복사되었습니다."


def test_template_button_is_disabled_without_image(panel: PhotoPanel) -> None:
    """선택이 없으면 꺼져 있고, 눌러도 복사하지 않는지 확인한다."""
    assert not panel._template_button.isEnabled()

    clipboard = _click_template_button(panel)

    clipboard.setText.assert_not_called()
    assert panel.findChild(_Toast) is None


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        ([], False),
        (["a.png"], True),
        (["도형.emf"], True),
        (["a.png", "b.jpg"], True),
        (["a.png", "movie.mp4"], True),
        (["movie.mp4"], False),
        (["a.mp4", "b.mov"], False),
    ],
)
def test_template_button_follows_selection(
    panel: PhotoPanel, tmp_path: Path, names: list[str], expected: bool
) -> None:
    """선택한 파일에 사진이 있을 때만 켜지는지 확인한다."""
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_bytes(b"x")

    panel.set_images(paths)

    assert panel._template_button.isEnabled() is expected


def test_template_button_turns_off_when_selection_is_cleared(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """선택이 풀리면 다시 꺼지는지 확인한다."""
    panel.set_images([_save_image(tmp_path / "사진.png")])
    assert panel._template_button.isEnabled()

    panel.set_images([])

    assert not panel._template_button.isEnabled()


def test_multiple_images_keep_photoshop_off_and_template_on(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """여러 장을 선택하면 포토샵 버튼은 꺼지고 템플릿 버튼은 켜지는지 확인한다."""
    panel.set_images(
        [_save_image(tmp_path / "a.png"), _save_image(tmp_path / "b.png")]
    )

    assert not _app_button(panel).isEnabled()
    assert panel._template_button.isEnabled()


def test_template_button_does_not_run_external_app(
    panel: PhotoPanel, tmp_path: Path, fake_open_command: MagicMock
) -> None:
    """템플릿 복사가 포토샵 실행으로 이어지지 않는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png"))

    _click_template_button(panel)

    fake_open_command.assert_not_called()


def test_template_read_failure_warns_and_keeps_clipboard(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """파일을 읽지 못하면 경고하고 클립보드와 알림을 건드리지 않는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with (
        patch(
            "ui.photo_panel.load_image_analysis_template",
            side_effect=OSError("없음"),
        ),
        patch("ui.photo_panel.warn") as warning,
    ):
        clipboard = _click_template_button(panel)

    warning.assert_called_once()
    clipboard.setText.assert_not_called()
    assert panel.findChild(_Toast) is None


def test_main_window_template_button_follows_clicked_image(
    main_window: MainWindow,
) -> None:
    """메인 창에서 사진을 누르면 켜지고, 폴더를 옮기면 꺼지는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    panel = main_window.findChild(PhotoPanel)
    assert not panel._template_button.isEnabled()

    _click_icon(browser, "a.png")
    assert panel._template_button.isEnabled()

    browser.set_project(None)
    assert not panel._template_button.isEnabled()


def test_disabled_button_runs_nothing(
    panel: PhotoPanel, fake_open_command: MagicMock
) -> None:
    """사진을 선택하지 않으면 버튼을 눌러도 실행하지 않는지 확인한다."""
    _app_button(panel).click()

    fake_open_command.assert_not_called()


def test_failed_open_warns(
    panel: PhotoPanel, tmp_path: Path, fake_open_command: MagicMock
) -> None:
    """프로그램을 찾지 못해 명령이 실패하면 경고하는지 확인한다."""
    fake_open_command.return_value = FAILURE_COMMAND
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with patch("ui.photo_panel.warn") as warning:
        _app_button(panel).click()
        assert _wait_until(lambda: panel._open_process is None)

    warning.assert_called_once()
    assert "Adobe Photoshop" in warning.call_args.args[2]


def test_command_that_cannot_start_warns(
    panel: PhotoPanel, tmp_path: Path, fake_open_command: MagicMock
) -> None:
    """명령 자체를 실행하지 못해도 경고하고 다시 시도할 수 있는지 확인한다."""
    fake_open_command.return_value = [str(tmp_path / "없는명령")]
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with patch("ui.photo_panel.warn") as warning:
        _app_button(panel).click()
        assert _wait_until(lambda: panel._open_process is None)

    warning.assert_called_once()

    fake_open_command.return_value = SUCCESS_COMMAND
    with patch("ui.photo_panel.warn") as warning:
        _app_button(panel).click()
        assert _wait_until(lambda: panel._open_process is None)
    warning.assert_not_called()


def test_clicking_while_opening_runs_only_once(
    panel: PhotoPanel, tmp_path: Path, fake_open_command: MagicMock
) -> None:
    """실행 중에 다시 눌러도 한 번만 실행하는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "사진.png"))

    _app_button(panel).click()
    _app_button(panel).click()

    fake_open_command.assert_called_once()
    assert _wait_until(lambda: panel._open_process is None)


@pytest.fixture
def browser(app: QApplication, tmp_path: Path) -> Iterator[FolderBrowser]:
    """사진 두 장과 폴더가 있는 프로젝트를 표시 중인 화면을 제공한다."""
    project = tmp_path / "프로젝트"
    (project / "Work").mkdir(parents=True)
    _save_image(project / "a.png")
    _save_image(project / "b.png")
    (project / "Work" / "c.png").write_bytes(b"x")

    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget.show()
    yield widget
    widget.stop_thumbnail_loading()
    widget.close()


def _click_icon(browser: FolderBrowser, name: str) -> None:
    """아이콘 목록에서 이름이 같은 항목을 마우스로 누른다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    QTest.mouseClick(
        folder_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=folder_list.visualItemRect(item).center(),
    )


def _click_row(browser: FolderBrowser, name: str) -> None:
    """자세히 표에서 이름이 같은 줄을 마우스로 누른다."""
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    QTest.mouseClick(
        tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=tree.visualItemRect(item).center(),
    )


def _record_selection(browser: FolderBrowser) -> list[Path | None]:
    """image_selected로 받은 값을 차례로 담는 목록을 반환한다."""
    received: list[Path | None] = []
    browser.images_selected.connect(
        lambda paths: received.append(paths[0] if paths else None)
    )
    return received


def _selected_icons(browser: FolderBrowser) -> list[str]:
    """아이콘 목록에서 선택된 항목의 이름을 반환한다."""
    return [item.text() for item in browser.findChild(QListWidget).selectedItems()]


def _selected_rows(browser: FolderBrowser) -> list[str]:
    """자세히 표에서 선택된 줄의 이름을 반환한다."""
    return [item.text(0) for item in browser.findChild(QTreeWidget).selectedItems()]


def test_clicking_image_selects_it(browser: FolderBrowser) -> None:
    """사진을 누르면 선택되고, 같은 사진은 한 번만 알리는지 확인한다."""
    received = _record_selection(browser)

    _click_icon(browser, "a.png")
    _click_icon(browser, "a.png")

    assert [path.name for path in received] == ["a.png"]
    assert _selected_icons(browser) == ["a.png"]


def test_only_one_image_is_selected(browser: FolderBrowser) -> None:
    """다른 사진을 누르면 선택이 옮겨 가는지 확인한다."""
    _click_icon(browser, "a.png")
    _click_icon(browser, "b.png")

    assert _selected_icons(browser) == ["b.png"]


def test_selection_is_shared_between_views(browser: FolderBrowser) -> None:
    """보기를 바꿔도 선택이 유지되고, 표에서 고른 사진이 아이콘에도 반영되는지 확인한다."""
    _click_icon(browser, "a.png")
    browser.set_view_mode(ViewMode.DETAILS)
    assert _selected_rows(browser) == ["a.png"]

    received = _record_selection(browser)
    _click_row(browser, "b.png")

    assert [path.name for path in received] == ["b.png"]
    assert _selected_rows(browser) == ["b.png"]
    assert _selected_icons(browser) == ["b.png"]


def test_clicking_folder_enters_without_selecting(browser: FolderBrowser) -> None:
    """폴더를 누르면 선택 표시 없이 들어가고, 사진 선택은 풀리는지 확인한다."""
    _click_icon(browser, "a.png")
    received = _record_selection(browser)

    _click_icon(browser, "Work")

    assert browser._path_label.text() == "프로젝트 / Work"
    assert received == [None]
    assert _selected_icons(browser) == []


def test_deleted_image_clears_selection(browser: FolderBrowser) -> None:
    """선택한 사진이 지워지면 선택이 풀리는지 확인한다."""
    _click_icon(browser, "a.png")
    selected = browser._selected_images[0]
    received = _record_selection(browser)

    selected.unlink()

    assert _wait_until(lambda: received == [None])
    assert _selected_icons(browser) == []


def test_other_file_change_keeps_selection(browser: FolderBrowser) -> None:
    """다른 파일이 지워져 목록을 다시 그려도 선택이 유지되는지 확인한다."""
    _click_icon(browser, "a.png")
    selected = browser._selected_images[0]
    received = _record_selection(browser)

    (selected.parent / "b.png").unlink()

    folder_list = browser.findChild(QListWidget)
    assert _wait_until(lambda: folder_list.count() == 2)
    assert received == []
    assert _selected_icons(browser) == ["a.png"]


def _double_click_icon(browser: FolderBrowser, name: str) -> None:
    """아이콘 목록에서 이름이 같은 항목을 더블클릭한다.

    mouseDClick은 첫 번째 클릭을 보내지 않으므로, 실제 더블클릭처럼 클릭을 먼저 보낸다.
    """
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    position = folder_list.visualItemRect(item).center()
    viewport = folder_list.viewport()
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=position)


def _record_activation(browser: FolderBrowser) -> list[Path]:
    """image_activated로 받은 값을 차례로 담는 목록을 반환한다."""
    received: list[Path] = []
    browser.image_activated.connect(received.append)
    return received


def test_double_clicking_image_activates_it(browser: FolderBrowser) -> None:
    """아이콘 보기에서 사진을 더블클릭하면 해당 사진을 알리는지 확인한다."""
    received = _record_activation(browser)

    _double_click_icon(browser, "a.png")

    assert [path.name for path in received] == ["a.png"]


def test_double_clicking_row_activates_image(browser: FolderBrowser) -> None:
    """자세히 보기에서 사진 줄을 더블클릭해도 알리는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    received = _record_activation(browser)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("b.png", Qt.MatchFlag.MatchExactly)[0]
    position = tree.visualItemRect(item).center()

    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=position)

    assert [path.name for path in received] == ["b.png"]


def test_single_click_does_not_activate_image(browser: FolderBrowser) -> None:
    """사진을 한 번만 누르면 열리지 않는지 확인한다."""
    received = _record_activation(browser)

    _click_icon(browser, "a.png")

    assert received == []


def test_double_click_right_after_entering_folder_is_ignored(
    browser: FolderBrowser,
) -> None:
    """폴더에 들어간 직후의 더블클릭으로 새 폴더의 사진이 열리지 않는지 확인한다."""
    folder = browser._current / "Work"
    _save_image(folder / "d.png")
    received = _record_activation(browser)

    _click_icon(browser, "Work")
    _double_click_icon(browser, "d.png")

    assert received == []

    QTest.qWait(QApplication.doubleClickInterval() + 50)
    _double_click_icon(browser, "d.png")

    assert [path.name for path in received] == ["d.png"]


def test_open_with_default_app_opens_file_url(
    panel: PhotoPanel, tmp_path: Path, fake_desktop_services: MagicMock
) -> None:
    """기본 프로그램 열기가 파일 주소로 요청되는지 확인한다."""
    path = tmp_path / "사진.png"

    with patch("ui.photo_panel.warn") as warning:
        panel.open_with_default_app(path)

    url = fake_desktop_services.openUrl.call_args.args[0]
    assert url.isLocalFile()
    assert Path(url.toLocalFile()) == path
    warning.assert_not_called()


def test_open_with_default_app_warns_on_failure(
    panel: PhotoPanel, tmp_path: Path, fake_desktop_services: MagicMock
) -> None:
    """열지 못하면 파일 이름이 있는 경고를 띄우는지 확인한다."""
    fake_desktop_services.openUrl.return_value = False

    with patch("ui.photo_panel.warn") as warning:
        panel.open_with_default_app(tmp_path / "도형.emf")

    warning.assert_called_once()
    assert "도형.emf" in warning.call_args.args[2]


def test_detached_launcher_does_not_wait_for_program(
    panel: PhotoPanel, tmp_path: Path, fake_launcher: MagicMock
) -> None:
    """Windows 방식은 종료를 기다리지 않아 다음 열기를 막지 않는지 확인한다."""
    fake_launcher.opens_detached = True
    fake_launcher.build_command.return_value = ["C:/앱/Photoshop.exe", "사진.png"]
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with patch.object(
        QProcess, "startDetached", return_value=(True, 123)
    ) as start_detached:
        _app_button(panel).click()
        _app_button(panel).click()

    assert start_detached.call_count == 2
    start_detached.assert_called_with("C:/앱/Photoshop.exe", ["사진.png"])
    assert panel._open_process is None


def test_detached_start_failure_warns(
    panel: PhotoPanel, tmp_path: Path, fake_launcher: MagicMock
) -> None:
    """Windows 방식에서 시작하지 못하면 경고하는지 확인한다."""
    fake_launcher.opens_detached = True
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with (
        patch.object(QProcess, "startDetached", return_value=(False, 0)),
        patch("ui.photo_panel.warn") as warning,
    ):
        _app_button(panel).click()

    warning.assert_called_once()
    assert "Adobe Photoshop" in warning.call_args.args[2]


def test_app_not_found_warns_without_running(
    panel: PhotoPanel, tmp_path: Path, fake_launcher: MagicMock
) -> None:
    """프로그램을 찾지 못하면 실행하지 않고 경고하는지 확인한다."""
    fake_launcher.build_command.side_effect = AppNotFoundError("Adobe Photoshop")
    panel.set_image(_save_image(tmp_path / "사진.png"))

    with patch("ui.photo_panel.warn") as warning:
        _app_button(panel).click()

    warning.assert_called_once()
    assert panel._open_process is None


@pytest.fixture
def main_window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """임시 폴더의 프로젝트를 쓰는 메인 창을 제공한다."""
    root = tmp_path / "root"
    (root / "test").mkdir(parents=True)
    _save_image(root / "test" / "a.png")
    with patch("ui.main_window.ProjectSelector", lambda: ProjectSelector(root)):
        window = MainWindow()
    window.show()
    yield window
    window.findChild(FolderBrowser).stop_thumbnail_loading()
    window.findChild(PhotoPanel).stop_preview_loading()
    window.close()


def test_main_window_double_click_opens_with_default_app(
    main_window: MainWindow, fake_desktop_services: MagicMock
) -> None:
    """메인 창에서 사진을 더블클릭하면 기본 프로그램으로 열고 칸에도 표시되는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    panel = main_window.findChild(PhotoPanel)

    _double_click_icon(browser, "a.png")

    url = fake_desktop_services.openUrl.call_args.args[0]
    assert Path(url.toLocalFile()).name == "a.png"
    assert panel._name_label.text() == "a.png"


def test_main_window_shows_selected_image_in_panel(
    main_window: MainWindow, fake_open_command: MagicMock
) -> None:
    """메인 창에서 사진을 누르면 오른쪽 칸에 나오고, 버튼으로 열리는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    panel = main_window.findChild(PhotoPanel)
    assert panel._preview_label.text() == NO_SELECTION_TEXT
    assert panel.x() > browser.x()

    _click_icon(browser, "a.png")

    assert panel._name_label.text() == "a.png"
    assert panel._preview_label.text() in (LOADING_TEXT, "")
    assert _wait_until(lambda: _preview_shown(panel))

    _app_button(panel).click()
    assert _wait_until(lambda: panel._open_process is None)
    assert fake_open_command.call_args.args[1].name == "a.png"
