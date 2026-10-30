"""사진 끌어내기 테스트. 운영체제의 실제 드래그는 띄우지 않고 시작 조건과 데이터를 확인한다."""

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QTreeWidget, QWidget

from ui.file_drag import DRAG_PIXMAP_SIZE, build_file_mime_data, start_file_drag
from ui.folder_browser import FolderBrowser, ViewMode
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.photo_panel import PhotoPanel
from ui.project_selector import ProjectSelector


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


def _save_image(path: Path) -> Path:
    """단색 사진 파일을 만들어 경로를 반환한다."""
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    return path


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


def _drag_on(widget: QWidget, start: QPoint, distance: int = 40) -> None:
    """위젯 위에서 눌러 끌었다가 놓는 동작을 보낸다."""
    _send_mouse(widget, QEvent.Type.MouseButtonPress, start)
    for step in (distance // 2, distance, distance + 5):
        _send_mouse(widget, QEvent.Type.MouseMove, start + QPoint(step, step))
    _send_mouse(
        widget,
        QEvent.Type.MouseButtonRelease,
        start + QPoint(distance, distance),
        Qt.MouseButton.NoButton,
    )


def test_mime_data_contains_file_url(app: QApplication) -> None:
    """드래그 데이터에 공백과 한글이 있는 파일 경로가 정확히 담기는지 확인한다."""
    path = Path("/작업 폴더/사진 1.png")

    mime_data = build_file_mime_data([path])

    assert mime_data.urls() == [QUrl.fromLocalFile(str(path))]
    assert mime_data.urls()[0].isLocalFile()
    assert mime_data.urls()[0].toLocalFile() == str(path)


def test_start_file_drag_allows_copy_only(app: QApplication, tmp_path: Path) -> None:
    """드래그가 복사로만 시작되고, 파일 경로와 축소 이미지를 담는지 확인한다."""
    source = QWidget()
    path = tmp_path / "사진.png"
    pixmap = QPixmap(800, 400)
    pixmap.fill(QColor("red"))

    with patch("ui.file_drag.QDrag") as drag_class:
        start_file_drag(source, [path], pixmap)

    drag = drag_class.return_value
    drag_class.assert_called_once_with(source)
    drag.exec.assert_called_once_with(Qt.DropAction.CopyAction)
    assert drag.setMimeData.call_args.args[0].urls()[0].toLocalFile() == str(path)
    shown = drag.setPixmap.call_args.args[0]
    assert shown.width() == DRAG_PIXMAP_SIZE.width()
    assert shown.height() == DRAG_PIXMAP_SIZE.height() // 2


def test_start_file_drag_uses_file_icon_without_pixmap(
    app: QApplication, tmp_path: Path
) -> None:
    """미리보기가 없는 파일은 일반 파일 아이콘으로 끌리는지 확인한다."""
    with patch("ui.file_drag.QDrag") as drag_class:
        start_file_drag(QWidget(), [tmp_path / "도형.emf"], None)

    assert not drag_class.return_value.setPixmap.call_args.args[0].isNull()


@pytest.fixture
def browser(app: QApplication, tmp_path: Path) -> Iterator[FolderBrowser]:
    """사진과 폴더가 있는 프로젝트를 표시 중인 화면을 제공한다."""
    project = tmp_path / "project"
    (project / "Work").mkdir(parents=True)
    _save_image(project / "a.png")
    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget.show()
    yield widget
    widget.stop_thumbnail_loading()
    widget.close()


def _icon_center(browser: FolderBrowser, name: str) -> QPoint:
    """아이콘 목록에서 항목의 가운데 위치를 반환한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    return folder_list.visualItemRect(item).center()


def _row_center(browser: FolderBrowser, name: str) -> QPoint:
    """자세히 표에서 줄의 가운데 위치를 반환한다."""
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    return tree.visualItemRect(item).center()


def test_dragging_image_icon_starts_file_drag(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """아이콘 보기에서 사진을 끌면 해당 파일로 드래그가 시작되는지 확인한다."""
    viewport = browser.findChild(QListWidget).viewport()

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_on(viewport, _icon_center(browser, "a.png"))

    start_drag.assert_called_once()
    assert start_drag.call_args.args[1] == [tmp_path / "project" / "a.png"]
    assert not start_drag.call_args.args[2].isNull()


def test_dragging_small_icon_starts_file_drag(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """작은 아이콘 보기에서도 끌 수 있는지 확인한다."""
    browser.set_view_mode(ViewMode.SMALL_ICONS)
    viewport = browser.findChild(QListWidget).viewport()

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_on(viewport, _icon_center(browser, "a.png"), distance=30)

    assert start_drag.call_args.args[1] == [tmp_path / "project" / "a.png"]


def test_dragging_detail_row_starts_file_drag(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """자세히 보기에서 줄을 끌면 드래그가 시작되고 정렬이 유지되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    order = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_on(tree.viewport(), _row_center(browser, "a.png"), distance=12)

    assert start_drag.call_args.args[1] == [tmp_path / "project" / "a.png"]
    assert [
        tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
    ] == order


def test_dragging_folder_does_nothing(browser: FolderBrowser) -> None:
    """폴더는 끌어도 드래그가 시작되지 않는지 확인한다."""
    viewport = browser.findChild(QListWidget).viewport()

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_on(viewport, _icon_center(browser, "Work"))

    start_drag.assert_not_called()


def test_short_click_selects_without_drag(browser: FolderBrowser) -> None:
    """살짝 누르기만 하면 드래그 없이 선택되는지 확인한다."""
    folder_list = browser.findChild(QListWidget)

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        QTest.mouseClick(
            folder_list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=_icon_center(browser, "a.png"),
        )

    start_drag.assert_not_called()
    assert [item.text() for item in folder_list.selectedItems()] == ["a.png"]


def test_mime_data_contains_all_files(app: QApplication) -> None:
    """여러 파일이 드래그 데이터에 모두 담기는지 확인한다."""
    paths = [Path("/폴더/a.png"), Path("/폴더/b.png")]

    urls = build_file_mime_data(paths).urls()

    assert [url.toLocalFile() for url in urls] == ["/폴더/a.png", "/폴더/b.png"]


def test_dragging_selected_images_carries_all(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """여러 장을 선택해 끌면 선택한 파일 모두 전달되는지 확인한다."""
    project = tmp_path / "project"
    _save_image(project / "b.png")
    browser.set_project(project)
    folder_list = browser.findChild(QListWidget)
    QTest.mouseClick(
        folder_list.viewport(),
        Qt.MouseButton.LeftButton,
        pos=_icon_center(browser, "a.png"),
    )
    QTest.mouseClick(
        folder_list.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        _icon_center(browser, "b.png"),
    )

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _drag_on(folder_list.viewport(), _icon_center(browser, "a.png"))

    start_drag.assert_called_once()
    assert sorted(path.name for path in start_drag.call_args.args[1]) == [
        "a.png",
        "b.png",
    ]


def test_rubber_band_from_empty_space_selects_without_drag(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """빈 공간에서 시작해 끌면 드래그 없이 범위 안의 사진이 선택되는지 확인한다."""
    project = tmp_path / "project"
    _save_image(project / "b.png")
    browser.set_project(project)
    folder_list = browser.findChild(QListWidget)
    viewport = folder_list.viewport()
    first = _icon_center(browser, "a.png")
    second = _icon_center(browser, "b.png")
    start = QPoint(second.x() + 200, second.y() + 200)

    with patch("ui.folder_browser.start_file_drag") as start_drag:
        _send_mouse(viewport, QEvent.Type.MouseButtonPress, start)
        for point in (QPoint(start.x() - 50, start.y() - 50), second, first):
            _send_mouse(viewport, QEvent.Type.MouseMove, point)
        _send_mouse(
            viewport, QEvent.Type.MouseButtonRelease, first, Qt.MouseButton.NoButton
        )

    start_drag.assert_not_called()
    assert sorted(path.name for path in browser._selected_images) == ["a.png", "b.png"]


@pytest.fixture
def panel(app: QApplication) -> Iterator[PhotoPanel]:
    """화면에 표시된 사진 칸을 제공한다."""
    widget = PhotoPanel()
    widget.resize(260, 500)
    widget.show()
    yield widget
    widget.stop_preview_loading()
    widget.close()


def test_dragging_preview_starts_file_drag(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """오른쪽 칸의 미리보기를 끌면 해당 파일로 드래그가 시작되는지 확인한다."""
    path = _save_image(tmp_path / "a.png")
    panel.set_image(path)
    label = panel._preview_label

    with patch("ui.photo_panel.start_file_drag") as start_drag:
        _drag_on(label, label.rect().center())

    start_drag.assert_called_once()
    assert start_drag.call_args.args[1] == [path]


def test_short_press_on_preview_does_not_drag(
    panel: PhotoPanel, tmp_path: Path
) -> None:
    """미리보기를 살짝 움직이기만 하면 드래그가 시작되지 않는지 확인한다."""
    panel.set_image(_save_image(tmp_path / "a.png"))
    label = panel._preview_label

    with patch("ui.photo_panel.start_file_drag") as start_drag:
        center = label.rect().center()
        _send_mouse(label, QEvent.Type.MouseButtonPress, center)
        _send_mouse(label, QEvent.Type.MouseMove, center + QPoint(1, 0))
        _send_mouse(
            label, QEvent.Type.MouseButtonRelease, center, Qt.MouseButton.NoButton
        )
        _send_mouse(label, QEvent.Type.MouseMove, center + QPoint(50, 50))

    start_drag.assert_not_called()


def test_empty_preview_does_not_drag(panel: PhotoPanel) -> None:
    """선택한 사진이 없으면 끌어도 드래그가 시작되지 않는지 확인한다."""
    label = panel._preview_label

    with patch("ui.photo_panel.start_file_drag") as start_drag:
        _drag_on(label, label.rect().center())

    start_drag.assert_not_called()


def test_main_window_ignores_drag_from_inside(
    app: QApplication, tmp_path: Path
) -> None:
    """프로그램 안에서 끈 사진은 받지 않고, 밖의 파일만 받는지 확인한다."""
    root = tmp_path / "root"
    root.mkdir()
    with patch("ui.main_window.ProjectSelector", lambda: ProjectSelector(root)):
        window = MainWindow()
    mime_data = build_file_mime_data([tmp_path / "a.png"])

    inside_event = MagicMock()
    inside_event.source.return_value = window
    inside_event.mimeData.return_value = mime_data
    window.dragEnterEvent(inside_event)
    inside_event.acceptProposedAction.assert_not_called()

    outside_event = MagicMock()
    outside_event.source.return_value = None
    outside_event.mimeData.return_value = mime_data
    window.dragEnterEvent(outside_event)
    outside_event.acceptProposedAction.assert_called_once()
