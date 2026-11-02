"""밖에서 끌어다 놓아 복사하기 테스트. 임시 폴더와 가짜 드래그 이벤트를 사용한다."""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QImage,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QTreeWidget,
    QWidget,
)

from core.photo_copy import copy_files_to_drop_target, drop_destination
from ui.copy_confirm_dialog import MAX_THUMBNAILS, FOLDER_NOTE, CopyConfirmDialog
from ui.file_drag import build_file_mime_data
from ui.folder_browser import FolderBrowser, ViewMode
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.photo_panel import PhotoPanel
from ui.project_selector import ProjectSelector
from ui.toast import _Toast
from ui.video_thumbnail import VideoThumbnailGrabber


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
    ("file_name", "target", "expected"),
    [
        ("a.png", "Work", "Work/Image"),
        ("a.JPG", "Work", "Work/Image"),
        ("a.emf", "Work", "Work/Image"),
        ("a.mp4", "Work", "Work/Video"),
        ("a.MOV", "Work", "Work/Video"),
        ("a.png", "Reference", "Reference/Image"),
        ("a.mp4", "Reference", "Reference/Video"),
    ],
)
def test_drop_destination_follows_rules(
    tmp_path: Path, file_name: str, target: str, expected: str
) -> None:
    """놓는 곳과 파일 종류에 맞는 폴더를 고르는지 확인한다."""
    assert drop_destination(Path(file_name), tmp_path, target) == tmp_path / expected


def test_copy_to_work_splits_images_and_videos(tmp_path: Path) -> None:
    """Work에 놓으면 사진과 동영상을 나눠 복사하고, 원본은 남는지 확인한다."""
    sources = _write_files(tmp_path / "media", "a.png", "b.mp4")

    summary = copy_files_to_drop_target(sources, tmp_path, "Work")

    assert summary.copied_count == 2
    assert _names(tmp_path / "Work" / "Image") == {"a.png"}
    assert _names(tmp_path / "Work" / "Video") == {"b.mp4"}
    assert all(source.exists() for source in sources)


def test_copy_to_reference_splits_images_and_videos(tmp_path: Path) -> None:
    """Reference에 놓아도 사진과 동영상을 나눠 복사하는지 확인한다."""
    sources = _write_files(tmp_path / "media", "a.png", "b.mp4")

    copy_files_to_drop_target(sources, tmp_path, "Reference")

    assert _names(tmp_path / "Reference") == {"Image", "Video"}
    assert _names(tmp_path / "Reference" / "Image") == {"a.png"}
    assert _names(tmp_path / "Reference" / "Video") == {"b.mp4"}


def test_copy_does_not_overwrite_and_counts_existing(tmp_path: Path) -> None:
    """같은 이름은 덮어쓰지 않고 이미 있는 것으로 세는지 확인한다."""
    source = _write_files(tmp_path / "media", "a.png")[0]
    existing = _write_files(tmp_path / "Work" / "Image", "a.png")[0]
    existing.write_text("기존")

    summary = copy_files_to_drop_target([source], tmp_path, "Work")

    assert summary.existing_count == 1
    assert existing.read_text() == "기존"


def test_dropping_file_on_its_own_folder_keeps_it(tmp_path: Path) -> None:
    """이미 Work/Image에 있는 파일을 Work에 놓아도 파일이 그대로인지 확인한다."""
    source = _write_files(tmp_path / "Work" / "Image", "a.png")[0]

    summary = copy_files_to_drop_target([source], tmp_path, "Work")

    assert summary.existing_count == 1
    assert source.read_text() == "a.png"


def test_copy_failure_is_reported(tmp_path: Path) -> None:
    """Work라는 파일이 있어 폴더를 만들 수 없으면 실패로 알리는지 확인한다."""
    source = _write_files(tmp_path / "media", "a.png")[0]
    (tmp_path / "Work").write_text("파일")

    summary = copy_files_to_drop_target([source], tmp_path, "Work")

    assert summary.copied_count == 0
    assert "a.png" in summary.failures[0]
    assert (tmp_path / "Work").read_text() == "파일"


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture(autouse=True)
def fake_video_grabber() -> Iterator[MagicMock]:
    """확인창이 실제 동영상 재생기를 쓰지 않도록 가짜로 바꾼다."""
    with patch("ui.copy_confirm_dialog.VideoThumbnailGrabber") as grabber:
        yield grabber


def _save_image(path: Path, width: int = 200, height: int = 100) -> Path:
    """단색 사진 파일을 만들어 경로를 반환한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
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


def _drag_event(
    paths: list[Path], source: object = None, position: QPoint = QPoint(0, 0)
) -> MagicMock:
    """드래그 이벤트를 흉내 낸다. source가 None이면 프로그램 밖에서 끌어온 것이다."""
    event = MagicMock()
    event.source.return_value = source
    event.mimeData.return_value = build_file_mime_data(paths)
    event.position.return_value.toPoint.return_value = position
    return event


def _labels(widget: QWidget) -> list[str]:
    """위젯 안의 문구들을 반환한다."""
    return [label.text() for label in widget.findChildren(QLabel)]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """맨 위에 Work, Reference, Document 폴더와 사진이 있는 프로젝트를 반환한다."""
    project_path = tmp_path / "project"
    (project_path / "Work").mkdir(parents=True)
    (project_path / "Reference").mkdir()
    media = project_path / "Document" / "PPT_Break" / "media"
    (media / "Work").mkdir(parents=True)
    _save_image(media / "a.png")
    _save_image(project_path / "top.png")
    return project_path


@pytest.fixture
def browser(app: QApplication, project: Path) -> Iterator[FolderBrowser]:
    """프로젝트 맨 위를 보고 있는 화면을 제공한다."""
    widget = FolderBrowser()
    widget.resize(800, 500)
    widget.set_project(project)
    widget.show()
    yield widget
    widget.stop_thumbnail_loading()
    widget.wait_for_photo_copy()
    widget.close()


def _icon_center(browser: FolderBrowser, name: str) -> QPoint:
    """아이콘 목록에서 항목의 가운데 위치를 반환한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    return folder_list.visualItemRect(item).center()


class _AcceptingDialog:
    """화면에 뜨지 않고 정해진 응답을 반환하는 확인창."""

    created: list[tuple[str, list[Path]]] = []
    sorts_by_kind: list[bool] = []
    result = QDialog.DialogCode.Accepted

    def __init__(
        self,
        target_name: str,
        paths: list[Path],
        _parent: object,
        sorts_by_kind: bool = True,
    ) -> None:
        """받은 값을 기록한다."""
        _AcceptingDialog.created.append((target_name, paths))
        _AcceptingDialog.sorts_by_kind.append(sorts_by_kind)

    def exec(self) -> QDialog.DialogCode:
        """정해진 응답을 반환한다."""
        return _AcceptingDialog.result

    def deleteLater(self) -> None:
        """지울 것이 없다."""


@pytest.fixture
def fake_dialog() -> Iterator[type[_AcceptingDialog]]:
    """확인창을 가짜로 바꾼다."""
    _AcceptingDialog.created = []
    _AcceptingDialog.sorts_by_kind = []
    _AcceptingDialog.result = QDialog.DialogCode.Accepted
    with patch("ui.folder_browser.CopyConfirmDialog", _AcceptingDialog):
        yield _AcceptingDialog


def test_cancel_copies_nothing(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """확인창에서 Cancel을 누르면 아무것도 복사하지 않는지 확인한다."""
    fake_dialog.result = QDialog.DialogCode.Rejected

    browser.handle_files_dropped("Reference", [project / "top.png"])

    assert _wait_until(lambda: bool(fake_dialog.created))
    QTest.qWait(20)
    assert browser._copy_worker is None
    assert _names(project / "Reference") == set()
    assert browser.findChild(_Toast) is None


def test_existing_file_shows_toast(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """이미 있으면 복사한 폴더 이름으로 알리는지 확인한다."""
    _write_files(project / "Work" / "Image", "top.png")

    browser.handle_files_dropped("Work", [project / "top.png"])

    assert _wait_until(lambda: browser.findChild(_Toast) is not None)
    assert browser.findChild(_Toast).text() == "이미 Work 폴더에 있습니다"


def test_drop_during_copy_shows_toast_without_dialog(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """복사 중에 또 놓으면 묻지 않고 알림만 띄우는지 확인한다."""
    browser._copy_worker = object()

    browser._confirm_file_copy("Work", [project / "top.png"])

    assert fake_dialog.created == []
    assert browser.findChild(_Toast).text() == "복사 중입니다"
    browser._copy_worker = None


@pytest.mark.parametrize("name", ["Work", "Reference"])
def test_icon_drop_on_target_folder_requests_copy(
    browser: FolderBrowser,
    project: Path,
    fake_dialog: type[_AcceptingDialog],
    name: str,
) -> None:
    """맨 위의 Work, Reference 아이콘 위에 놓으면 받아서 복사를 묻는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    position = _icon_center(browser, name)
    move = _drag_event([project / "top.png"], position=position)
    folder_list.dragMoveEvent(move)
    move.accept.assert_called_once()

    folder_list.dropEvent(_drag_event([project / "top.png"], position=position))

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == name
    assert _wait_until(lambda: browser._copy_worker is None)


def _drop_on_icon(
    browser: FolderBrowser, project: Path, name: str
) -> tuple[MagicMock, MagicMock]:
    """아이콘 위로 끌고 놓는 동작을 보내고, 두 이벤트를 반환한다."""
    folder_list = browser.findChild(QListWidget)
    position = _icon_center(browser, name)
    move = _drag_event([project / "top.png"], position=position)
    folder_list.dragMoveEvent(move)
    drop = _drag_event([project / "top.png"], position=position)
    folder_list.dropEvent(drop)
    return move, drop


def test_icon_drop_on_other_folder_warns_without_copying(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Document 위에 놓으면 경고창을 띄우고 복사하지 않는지 확인한다."""
    with patch("ui.folder_browser.warn") as warning:
        move, _drop = _drop_on_icon(browser, project, "Document")
        assert _wait_until(lambda: warning.called)

    move.accept.assert_called_once()
    assert warning.call_args.args[2] == "Work 폴더와 Reference 폴더에만 이동 가능합니다."
    assert fake_dialog.created == []
    assert _names(project / "Document") == {"PPT_Break"}


def test_icon_drop_outside_folders_goes_to_other_handling(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """사진 위와 빈 공간에 놓으면 복사나 경고 없이 PPT 처리로 넘기는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)
    path = project / "top.png"

    with patch("ui.folder_browser.warn") as warning:
        for position in (_icon_center(browser, "top.png"), QPoint(780, 480)):
            folder_list.dropEvent(_drag_event([path], position=position))
        QTest.qWait(20)

    assert received == [[path], [path]]
    warning.assert_not_called()
    assert fake_dialog.created == []


def test_pptx_on_target_folder_goes_to_pptx_handling(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """PPTX만 끌어와 Work 위에 놓아도 복사가 아닌 PPT 처리로 넘기는지 확인한다."""
    pptx = tmp_path / "제안서.pptx"
    pptx.write_bytes(b"x")
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)

    browser.findChild(QListWidget).dropEvent(
        _drag_event([pptx], position=_icon_center(browser, "Work"))
    )
    QTest.qWait(20)

    assert received == [[pptx]]
    assert fake_dialog.created == []


@pytest.mark.parametrize("name", ["Work", "Reference", "Document", "top.png"])
def test_internal_drag_cannot_be_dropped_anywhere_in_list(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog], name: str
) -> None:
    """프로그램 안의 파일은 어느 폴더에도 놓을 수 없고, 경고도 없는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    path = project / "top.png"
    position = _icon_center(browser, name)
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)
    enter = _drag_event([path], source=folder_list, position=position)
    move = _drag_event([path], source=folder_list, position=position)
    drop = _drag_event([path], source=folder_list, position=position)

    with patch("ui.folder_browser.warn") as warning:
        folder_list.dragEnterEvent(enter)
        folder_list.dragMoveEvent(move)
        folder_list.dropEvent(drop)
        QTest.qWait(20)

    enter.ignore.assert_called_once()
    move.ignore.assert_called_once()
    drop.ignore.assert_called_once()
    warning.assert_not_called()
    assert received == []
    assert fake_dialog.created == []


def test_same_named_folder_in_subfolder_warns(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """하위 폴더 안의 같은 이름 폴더는 대상이 아니어서 경고하는지 확인한다."""
    media = project / "Document" / "PPT_Break" / "media"
    browser._navigate_to(media)

    with patch("ui.folder_browser.warn") as warning:
        drop = _drag_event([media / "a.png"], position=_icon_center(browser, "Work"))
        browser.findChild(QListWidget).dropEvent(drop)
        assert _wait_until(lambda: warning.called)

    assert fake_dialog.created == []


def test_icon_highlights_only_while_dragging_over_target(
    browser: FolderBrowser, project: Path
) -> None:
    """대상 폴더 위에서만 강조되고 벗어나면 지워지는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems("Work", Qt.MatchFlag.MatchExactly)[0]
    event = _drag_event([project / "top.png"], position=_icon_center(browser, "Work"))

    folder_list.dragMoveEvent(event)
    assert item.background().style() != Qt.BrushStyle.NoBrush

    folder_list.dragLeaveEvent(MagicMock())
    assert item.background().style() == Qt.BrushStyle.NoBrush


@pytest.mark.parametrize("name", ["Work", "Reference", "Document"])
def test_any_folder_highlights_while_dragging_over_it(
    browser: FolderBrowser, project: Path, name: str
) -> None:
    """끌고 올라간 폴더가 강조되고, 벗어나면 지워지는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems(name, Qt.MatchFlag.MatchExactly)[0]
    event = _drag_event([project / "top.png"], position=_icon_center(browser, name))

    folder_list.dragMoveEvent(event)
    assert item.background().style() != Qt.BrushStyle.NoBrush

    folder_list.dragMoveEvent(
        _drag_event([project / "top.png"], position=QPoint(780, 480))
    )
    assert item.background().style() == Qt.BrushStyle.NoBrush


def test_highlight_moves_to_new_folder(
    browser: FolderBrowser, project: Path
) -> None:
    """다른 폴더로 옮겨 가면 강조도 옮겨 가는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    work = folder_list.findItems("Work", Qt.MatchFlag.MatchExactly)[0]
    reference = folder_list.findItems("Reference", Qt.MatchFlag.MatchExactly)[0]
    path = project / "top.png"

    folder_list.dragMoveEvent(_drag_event([path], position=_icon_center(browser, "Work")))
    folder_list.dragMoveEvent(
        _drag_event([path], position=_icon_center(browser, "Reference"))
    )

    assert work.background().style() == Qt.BrushStyle.NoBrush
    assert reference.background().style() != Qt.BrushStyle.NoBrush


def test_internal_or_pptx_drag_does_not_highlight(
    browser: FolderBrowser, project: Path, tmp_path: Path
) -> None:
    """프로그램 안의 파일과 PPTX를 끌 때는 폴더를 강조하지 않는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems("Work", Qt.MatchFlag.MatchExactly)[0]
    position = _icon_center(browser, "Work")
    pptx = tmp_path / "제안서.pptx"
    pptx.write_bytes(b"x")

    folder_list.dragMoveEvent(
        _drag_event([project / "top.png"], source=folder_list, position=position)
    )
    folder_list.dragMoveEvent(_drag_event([pptx], position=position))

    assert item.background().style() == Qt.BrushStyle.NoBrush


def _send_real_drag(
    view: QWidget, position: QPoint, paths: list[Path], drop: bool = True
) -> QDragMoveEvent:
    """실제 드래그와 같이 Qt 이벤트를 표시 영역으로 보내고, 이동 이벤트를 반환한다."""
    viewport = view.viewport()
    mime_data = build_file_mime_data(paths)
    action = Qt.DropAction.CopyAction
    button = Qt.MouseButton.LeftButton
    modifier = Qt.KeyboardModifier.NoModifier
    enter = QDragEnterEvent(position, action, mime_data, button, modifier)
    QApplication.sendEvent(viewport, enter)
    assert enter.isAccepted()
    move = QDragMoveEvent(position, action, mime_data, button, modifier)
    QApplication.sendEvent(viewport, move)
    if drop:
        drop_event = QDropEvent(QPointF(position), action, mime_data, button, modifier)
        QApplication.sendEvent(viewport, drop_event)
    return move


def test_viewports_accept_real_drags(browser: FolderBrowser) -> None:
    """목록과 표의 표시 영역이 실제 드래그를 받도록 설정되어 있는지 확인한다."""
    assert browser.findChild(QListWidget).viewport().acceptDrops()
    assert browser.findChild(QTreeWidget).viewport().acceptDrops()


def test_real_drag_on_work_icon_copies_by_kind(
    browser: FolderBrowser,
    project: Path,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
) -> None:
    """실제 이벤트 경로로 Work 아이콘에 놓으면 Image, Video로 복사되는지 확인한다."""
    photo = _save_image(tmp_path / "밖" / "photo.png")
    video = _write_files(tmp_path / "밖", "movie.mp4")[0]

    _send_real_drag(
        browser.findChild(QListWidget),
        _icon_center(browser, "Work"),
        [photo, video],
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == "Work"
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (project / "Work" / "Image" / "photo.png").exists()
    assert (project / "Work" / "Video" / "movie.mp4").exists()
    assert photo.exists()


def test_real_drag_on_reference_copies_by_kind(
    browser: FolderBrowser,
    project: Path,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
) -> None:
    """맨 위의 Reference에 놓으면 종류에 맞는 폴더로 복사되는지 확인한다."""
    photo = _save_image(tmp_path / "밖" / "photo.png")

    _send_real_drag(
        browser.findChild(QListWidget), _icon_center(browser, "Reference"), [photo]
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == "Reference"
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (project / "Reference" / "Image" / "photo.png").exists()


def test_real_drag_inside_reference_folder_copies_there(
    browser: FolderBrowser,
    project: Path,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
) -> None:
    """Reference 하위 폴더의 빈 공간에 놓으면 그 폴더로 복사되는지 확인한다."""
    folder = project / "Reference" / "Image"
    (folder / "시안").mkdir(parents=True)
    browser._navigate_to(folder)
    photo = _save_image(tmp_path / "밖" / "photo.png")

    _send_real_drag(browser.findChild(QListWidget), QPoint(780, 480), [photo])

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == "Image"
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (folder / "photo.png").exists()


def test_real_drag_on_pub_shows_warning(
    browser: FolderBrowser, project: Path, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """실제 이벤트 경로로 Document에 놓으면 경고창이 뜨는지 확인한다."""
    photo = _save_image(tmp_path / "밖" / "photo.png")

    with patch("ui.folder_browser.warn") as warning:
        _send_real_drag(
            browser.findChild(QListWidget), _icon_center(browser, "Document"), [photo]
        )
        assert _wait_until(lambda: warning.called)

    assert warning.call_args.args[2] == "Work 폴더와 Reference 폴더에만 이동 가능합니다."
    assert fake_dialog.created == []


def test_real_drag_on_detail_row_copies(
    browser: FolderBrowser,
    project: Path,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
) -> None:
    """실제 이벤트 경로로 자세히 보기의 Work 줄에 놓아도 복사되는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("Work", Qt.MatchFlag.MatchExactly)[0]
    photo = _save_image(tmp_path / "밖" / "photo.png")

    _send_real_drag(tree, tree.visualItemRect(item).center(), [photo])

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (project / "Work" / "Image" / "photo.png").exists()


def test_real_drag_highlights_hovered_folder(
    browser: FolderBrowser, project: Path, tmp_path: Path
) -> None:
    """실제 이벤트 경로로 끌고 올라가면 그 폴더가 강조되는지 확인한다."""
    folder_list = browser.findChild(QListWidget)
    item = folder_list.findItems("Work", Qt.MatchFlag.MatchExactly)[0]
    photo = _save_image(tmp_path / "밖" / "photo.png")

    _send_real_drag(folder_list, _icon_center(browser, "Work"), [photo], drop=False)

    assert item.background().style() != Qt.BrushStyle.NoBrush


def test_detail_row_drop_on_target_requests_copy(
    browser: FolderBrowser, project: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """자세히 보기의 폴더 줄 위에 놓아도 복사를 묻는지 확인한다."""
    browser.set_view_mode(ViewMode.DETAILS)
    tree = browser.findChild(QTreeWidget)
    item = tree.findItems("Reference", Qt.MatchFlag.MatchExactly)[0]
    position = tree.visualItemRect(item).center()

    move = _drag_event([project / "top.png"], position=position)
    tree.dragMoveEvent(move)
    move.accept.assert_called_once()

    tree.dropEvent(_drag_event([project / "top.png"], position=position))

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == "Reference"
    assert _wait_until(lambda: browser._copy_worker is None)


def test_external_drag_is_accepted_by_list(
    browser: FolderBrowser, tmp_path: Path
) -> None:
    """밖에서 끌어온 파일은 목록이 받는지 확인한다."""
    enter = _drag_event([tmp_path / "a.png"])

    browser.findChild(QListWidget).dragEnterEvent(enter)

    enter.accept.assert_called_once()


def test_non_media_files_are_skipped_with_warning(
    browser: FolderBrowser, project: Path, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """사진과 함께 끌어온 다른 파일은 알리고, 사진만 복사하는지 확인한다."""
    memo = tmp_path / "메모.txt"
    memo.write_text("메모")
    image = project / "top.png"

    with patch("ui.folder_browser.warn") as warning:
        browser._confirm_file_copy("Work", [image, memo])

    assert "메모.txt" in warning.call_args.args[2]
    assert fake_dialog.created == [("Work", [image])]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(project / "Work" / "Image") == {"top.png"}


def test_copying_file_from_outside_project(
    browser: FolderBrowser, project: Path, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """프로젝트 밖의 동영상을 놓으면 복사되고 원본이 남는지 확인한다."""
    outside = _write_files(tmp_path / "바탕화면", "영상.mp4")[0]

    browser.findChild(QListWidget).dropEvent(
        _drag_event([outside], position=_icon_center(browser, "Work"))
    )

    assert _wait_until(lambda: (project / "Work" / "Video" / "영상.mp4").exists())
    assert _wait_until(lambda: browser._copy_worker is None)
    assert outside.exists()


def _project_folder(browser: FolderBrowser, *parts: str) -> Path:
    """프로젝트 안의 폴더 경로를 반환한다."""
    return browser._project.joinpath(*parts)


@pytest.mark.parametrize(
    ("view", "target"),
    [
        (("Work",), "Work"),
        (("Reference",), "Reference"),
    ],
)
def test_area_drop_inside_target_copies_by_kind(
    browser: FolderBrowser,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
    view: tuple[str, ...],
    target: str,
) -> None:
    """Work, Reference를 보면서 빈 공간에 놓으면 종류에 맞는 폴더로 복사되는지 확인한다."""
    folder = _project_folder(browser, *view)
    (folder / "내용").mkdir(parents=True)
    browser._navigate_to(folder)
    photo = _save_image(tmp_path / "밖" / "photo.png")
    video = _write_files(tmp_path / "밖", "movie.mp4")[0]

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo, video], position=QPoint(780, 480))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == target
    assert _wait_until(lambda: browser._copy_worker is None)
    project = browser._project
    assert (project / target / "Image" / "photo.png").exists()
    assert (project / target / "Video" / "movie.mp4").exists()


@pytest.mark.parametrize(
    "view",
    [("Work", "Image"), ("Work", "Video", "시안"), ("Reference", "자료")],
)
def test_area_drop_in_subfolder_copies_into_viewed_folder(
    browser: FolderBrowser,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
    view: tuple[str, ...],
) -> None:
    """하위 폴더를 보면서 빈 공간에 놓으면 지금 폴더로 들어가고 이름이 뜨는지 확인한다."""
    folder = _project_folder(browser, *view)
    (folder / "내용").mkdir(parents=True)
    browser._navigate_to(folder)
    photo = _save_image(tmp_path / "밖" / "photo.png")
    video = _write_files(tmp_path / "밖", "movie.mp4")[0]

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo, video], position=QPoint(780, 480))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == view[-1]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (folder / "photo.png").exists()
    assert (folder / "movie.mp4").exists()


@pytest.mark.parametrize("area", ["Work", "Reference"])
def test_drop_on_folder_icon_copies_into_that_folder(
    browser: FolderBrowser,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
    area: str,
) -> None:
    """안쪽 폴더 아이콘에 놓으면 종류와 상관없이 그 폴더로 들어가고 이름이 뜨는지 확인한다."""
    folder = _project_folder(browser, area, "시안")
    folder.mkdir(parents=True)
    browser._navigate_to(_project_folder(browser, area))
    photo = _save_image(tmp_path / "밖" / "photo.png")
    video = _write_files(tmp_path / "밖", "movie.mp4")[0]

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo, video], position=_icon_center(browser, "시안"))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert fake_dialog.created[0][0] == "시안"
    assert fake_dialog.sorts_by_kind == [False]
    assert _wait_until(lambda: browser._copy_worker is None)
    assert _names(folder) == {"photo.png", "movie.mp4"}
    assert not _project_folder(browser, area, "Image").exists()
    assert not _project_folder(browser, area, "Video").exists()
    assert "시안" in browser.findChild(_Toast).text()


def test_same_name_elsewhere_does_not_block_folder_drop(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Work/Image에 같은 이름이 있어도 다른 폴더에는 들어가는지 확인한다."""
    existing = _save_image(_project_folder(browser, "Work", "Image") / "photo.png")
    folder = _project_folder(browser, "Work", "시안")
    folder.mkdir(parents=True)
    browser._navigate_to(_project_folder(browser, "Work"))
    photo = _save_image(tmp_path / "밖" / "photo.png")

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo], position=_icon_center(browser, "시안"))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (folder / "photo.png").exists()
    assert existing.exists()


def test_same_name_in_chosen_folder_is_not_overwritten(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """고른 폴더에 같은 이름이 있으면 덮어쓰지 않는지 확인한다."""
    folder = _project_folder(browser, "Work", "시안")
    existing = _write_files(folder, "photo.png")[0]
    before = existing.read_bytes()
    browser._navigate_to(_project_folder(browser, "Work"))
    photo = _save_image(tmp_path / "밖" / "photo.png")

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo], position=_icon_center(browser, "시안"))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert _wait_until(lambda: browser._copy_worker is None)
    assert existing.read_bytes() == before


def test_cancel_folder_drop_copies_nothing(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """폴더에 놓고 Cancel을 누르면 복사하지 않는지 확인한다."""
    fake_dialog.result = QDialog.DialogCode.Rejected
    folder = _project_folder(browser, "Work", "시안")
    folder.mkdir(parents=True)
    browser._navigate_to(_project_folder(browser, "Work"))
    photo = _save_image(tmp_path / "밖" / "photo.png")

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo], position=_icon_center(browser, "시안"))
    )

    assert _wait_until(lambda: bool(fake_dialog.created))
    QTest.qWait(50)
    assert browser._copy_worker is None
    assert _names(folder) == set()


def test_dialog_hides_sorting_note_for_chosen_folder(
    app: QApplication, tmp_path: Path
) -> None:
    """고른 폴더에 넣을 때는 Image, Video 안내를 숨기는지 확인한다."""
    path = _save_image(tmp_path / "a.png")

    dialog = CopyConfirmDialog("시안", [path], sorts_by_kind=False)

    assert "'시안' 폴더로 복사하시겠습니까?" in _labels(dialog)
    assert FOLDER_NOTE not in _labels(dialog)
    dialog.close()


def test_drop_on_empty_target_folder_uses_browser_area(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """목록이 숨겨진 빈 폴더에서도 화면에 놓으면 복사되는지 확인한다."""
    folder = _project_folder(browser, "Work", "Image")
    folder.mkdir()
    browser._navigate_to(folder)
    assert browser.findChild(QListWidget).isHidden()
    photo = _save_image(tmp_path / "밖" / "photo.png")
    enter = _drag_event([photo])

    browser.dragEnterEvent(enter)
    enter.accept.assert_called_once()
    browser.dropEvent(_drag_event([photo]))

    assert _wait_until(lambda: bool(fake_dialog.created))
    assert _wait_until(lambda: browser._copy_worker is None)
    assert (folder / "photo.png").exists()


def test_area_drop_outside_targets_goes_to_other_handling(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Pub 안의 빈 공간처럼 대상 밖에서는 복사하지 않고 PPT 처리로 넘기는지 확인한다."""
    folder = _project_folder(browser, "Pub")
    (folder / "Image").mkdir(parents=True)
    browser._navigate_to(folder)
    photo = _save_image(tmp_path / "밖" / "photo.png")
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo], position=QPoint(780, 480))
    )
    QTest.qWait(20)

    assert received == [[photo]]
    assert fake_dialog.created == []


def test_folder_in_pub_warns(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Pub 안의 폴더 아이콘에 놓으면 경고하는지 확인한다."""
    folder = _project_folder(browser, "Pub")
    (folder / "Image").mkdir(parents=True)
    browser._navigate_to(folder)
    photo = _save_image(tmp_path / "밖" / "photo.png")

    with patch("ui.folder_browser.warn") as warning:
        browser.findChild(QListWidget).dropEvent(
            _drag_event([photo], position=_icon_center(browser, "Image"))
        )
        assert _wait_until(lambda: warning.called)

    assert fake_dialog.created == []


def test_pptx_inside_target_folder_goes_to_pptx_handling(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Work 안에 PPTX만 놓으면 복사가 아닌 PPT 처리로 넘기는지 확인한다."""
    browser._navigate_to(_project_folder(browser, "Work"))
    pptx = tmp_path / "제안서.pptx"
    pptx.write_bytes(b"x")
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)

    browser.dropEvent(_drag_event([pptx]))
    QTest.qWait(20)

    assert received == [[pptx]]
    assert fake_dialog.created == []


def test_internal_drag_on_empty_space_is_rejected_inside_target_folder(
    browser: FolderBrowser, fake_dialog: type[_AcceptingDialog]
) -> None:
    """Work 안의 빈 공간에는 프로그램 안의 파일을 놓아도 아무 일이 없는지 확인한다."""
    folder = _project_folder(browser, "Work")
    photo = _save_image(folder / "inside.png")
    browser._navigate_to(folder)
    move = _drag_event([photo], source=browser)
    drop = _drag_event([photo], source=browser)

    browser.dragMoveEvent(move)
    browser.dropEvent(drop)
    QTest.qWait(20)

    move.ignore.assert_called_once()
    drop.ignore.assert_called_once()
    assert fake_dialog.created == []
    assert photo.exists()


def test_browser_area_rejects_drop_without_project(
    browser: FolderBrowser, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """프로젝트가 없으면 복사 대상이 없어 PPT 처리로 넘기는지 확인한다."""
    browser.set_project(None)
    photo = _save_image(tmp_path / "밖" / "photo.png")
    received: list[list[Path]] = []
    browser.other_files_dropped.connect(received.append)

    browser.dropEvent(_drag_event([photo]))

    assert received == [[photo]]
    assert fake_dialog.created == []


@pytest.fixture
def main_window(
    app: QApplication, project: Path, tmp_path: Path
) -> Iterator[MainWindow]:
    """임시 폴더의 프로젝트를 쓰는 메인 창을 제공한다."""
    root = tmp_path / "root"
    root.mkdir()
    project.rename(root / "test")
    (root / "test" / "Pub").mkdir()
    with patch("ui.main_window.ProjectSelector", lambda: ProjectSelector(root)):
        window = MainWindow()
    window.show()
    yield window
    browser = window.findChild(FolderBrowser)
    browser.stop_thumbnail_loading()
    browser.wait_for_photo_copy()
    window.findChild(PhotoPanel).stop_preview_loading()
    window.close()


def test_main_window_has_browser_and_photo_panel_only(
    main_window: MainWindow,
) -> None:
    """왼쪽 칸 없이 폴더 화면이 맨 왼쪽에 있는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    photo_panel = main_window.findChild(PhotoPanel)
    assert main_window.width() == 960
    assert browser.mapTo(main_window, QPoint(0, 0)).x() < 40
    assert browser.x() < photo_panel.x()


def test_main_window_pptx_on_list_asks_to_save_ppt(
    main_window: MainWindow, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """메인 화면에 PPTX를 놓으면 PPT 저장 확인으로 이어지는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    pptx = tmp_path / "제안서.pptx"
    pptx.write_bytes(b"x")

    with patch("ui.main_window.confirm", return_value=False) as confirm_mock:
        browser.findChild(QListWidget).dropEvent(
            _drag_event([pptx], position=_icon_center(browser, "Work"))
        )
        assert _wait_until(lambda: confirm_mock.called)

    assert "PPT를 저장" in confirm_mock.call_args.args[2]
    assert fake_dialog.created == []


def test_main_window_drop_on_work_icon_copies_and_shows_toast(
    main_window: MainWindow, tmp_path: Path, fake_dialog: type[_AcceptingDialog]
) -> None:
    """밖의 사진을 Work 아이콘에 놓으면 복사하고 알림을 띄우는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    project = tmp_path / "root" / "test"
    photo = _save_image(tmp_path / "밖" / "photo.png")

    browser.findChild(QListWidget).dropEvent(
        _drag_event([photo], position=_icon_center(browser, "Work"))
    )

    assert _wait_until(lambda: (project / "Work" / "Image" / "photo.png").exists())
    assert _wait_until(lambda: browser._copy_worker is None)
    assert photo.exists()
    assert main_window.findChild(_Toast).text() == "파일 1개를 Work 폴더로 복사했습니다"


@pytest.mark.parametrize("name", ["Pub", "Document"])
def test_main_window_drop_on_pub_or_document_warns(
    main_window: MainWindow,
    tmp_path: Path,
    fake_dialog: type[_AcceptingDialog],
    name: str,
) -> None:
    """Pub, Document 아이콘에 놓으면 안내 문구의 경고창이 뜨는지 확인한다."""
    browser = main_window.findChild(FolderBrowser)
    photo = _save_image(tmp_path / "밖" / "photo.png")

    with patch("ui.folder_browser.warn") as warning:
        browser.findChild(QListWidget).dropEvent(
            _drag_event([photo], position=_icon_center(browser, name))
        )
        assert _wait_until(lambda: warning.called)

    assert warning.call_args.args[2] == "Work 폴더와 Reference 폴더에만 이동 가능합니다."
    assert fake_dialog.created == []


def test_dialog_shows_question_names_and_thumbnails(
    app: QApplication, tmp_path: Path
) -> None:
    """확인창에 질문과 이름이 나오고, 사진 썸네일이 표시되는지 확인한다."""
    path = _save_image(tmp_path / "a.png", 800, 400)

    dialog = CopyConfirmDialog("Reference", [path])
    label = dialog._thumbnail_labels[path]
    before = label.pixmap().cacheKey()

    assert "'Reference' 폴더로 복사하시겠습니까?" in _labels(dialog)
    assert "a.png" in _labels(dialog)
    assert FOLDER_NOTE in _labels(dialog)
    assert _wait_until(lambda: label.pixmap().cacheKey() != before)
    dialog.reject()


def test_dialog_explains_work_rule(app: QApplication, tmp_path: Path) -> None:
    """Work로 복사할 때는 사진과 동영상이 들어갈 곳을 안내하는지 확인한다."""
    dialog = CopyConfirmDialog("Work", [_save_image(tmp_path / "a.png")])

    assert FOLDER_NOTE in _labels(dialog)
    dialog.reject()


def test_dialog_loads_images_from_different_folders(
    app: QApplication, tmp_path: Path
) -> None:
    """밖에서 끌어온 사진이 여러 폴더에 있어도 썸네일이 모두 나오는지 확인한다."""
    first = _save_image(tmp_path / "가" / "a.png")
    second = _save_image(tmp_path / "나" / "a.png")

    dialog = CopyConfirmDialog("Work", [first, second])
    first_label = dialog._thumbnail_labels[first]
    second_label = dialog._thumbnail_labels[second]
    first_key = first_label.pixmap().cacheKey()
    second_key = second_label.pixmap().cacheKey()

    assert _wait_until(lambda: first_label.pixmap().cacheKey() != first_key)
    assert _wait_until(lambda: second_label.pixmap().cacheKey() != second_key)
    dialog.reject()


def test_dialog_limits_thumbnails_and_shows_rest_count(
    app: QApplication, tmp_path: Path
) -> None:
    """썸네일 수를 제한하고 나머지 개수를 표시하는지 확인한다."""
    paths = [tmp_path / f"{index}.png" for index in range(MAX_THUMBNAILS + 3)]
    for path in paths:
        path.write_bytes(b"x")

    dialog = CopyConfirmDialog("Work", paths)

    assert len(dialog._thumbnail_labels) == MAX_THUMBNAILS
    assert "외 3개" in _labels(dialog)
    dialog.reject()


def test_dialog_sends_only_videos_to_grabber(
    app: QApplication, tmp_path: Path, fake_video_grabber: MagicMock
) -> None:
    """동영상만 장면 가져오기로 보내고, 받은 장면을 표시하는지 확인한다."""
    image = _save_image(tmp_path / "a.png")
    video = tmp_path / "b.mp4"
    video.write_bytes(b"x")

    dialog = CopyConfirmDialog("Work", [image, video])

    fake_video_grabber.return_value.grab.assert_called_once_with([video])
    label = dialog._thumbnail_labels[video]
    before = label.pixmap().cacheKey()
    frame = QImage(192, 192, QImage.Format.Format_RGB32)
    frame.fill(QColor("blue"))
    dialog._on_video_thumbnail_ready(str(video), frame)
    assert label.pixmap().cacheKey() != before

    dialog.reject()
    fake_video_grabber.return_value.stop.assert_called()


@pytest.mark.parametrize(
    ("button", "expected"),
    [
        (QDialogButtonBox.StandardButton.Ok, QDialog.DialogCode.Accepted),
        (QDialogButtonBox.StandardButton.Cancel, QDialog.DialogCode.Rejected),
    ],
)
def test_dialog_buttons_return_result_and_center_on_window(
    app: QApplication,
    tmp_path: Path,
    button: QDialogButtonBox.StandardButton,
    expected: QDialog.DialogCode,
) -> None:
    """버튼을 누르면 창이 닫히고, 메인 창 가운데에 뜨는지 확인한다."""
    window = QWidget()
    window.setGeometry(100, 100, 960, 640)
    window.show()
    dialog = CopyConfirmDialog("Work", [_save_image(tmp_path / "a.png")], window)
    centers: list[QPoint] = []

    def check_and_click() -> None:
        """위치를 기록하고 버튼을 누른다."""
        centers.append(dialog.frameGeometry().center())
        dialog.findChild(QDialogButtonBox).button(button).click()

    QTimer.singleShot(0, check_and_click)

    assert dialog.exec() == expected
    delta = centers[0] - window.frameGeometry().center()
    assert abs(delta.x()) <= 1 and abs(delta.y()) <= 1
    window.close()


@pytest.fixture
def grabber_parts() -> Iterator[tuple[MagicMock, MagicMock]]:
    """재생기와 장면 수신기를 가짜로 바꾼다."""
    with (
        patch("ui.video_thumbnail.QMediaPlayer") as player_class,
        patch("ui.video_thumbnail.QVideoSink") as sink_class,
    ):
        player_class.Error = QMediaPlayer.Error
        yield player_class.return_value, sink_class.return_value


def _frame(width: int = 640, height: int = 360) -> MagicMock:
    """유효한 동영상 장면을 흉내 낸다."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    frame = MagicMock()
    frame.isValid.return_value = True
    frame.toImage.return_value = image
    return frame


def test_grabber_sends_first_frames_in_order(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """동영상을 차례로 열어, 첫 장면을 썸네일 크기로 보내는지 확인한다."""
    player, _sink = grabber_parts
    grabber = VideoThumbnailGrabber()
    received: list[tuple[str, QImage]] = []
    grabber.thumbnail_ready.connect(lambda path, image: received.append((path, image)))
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"

    grabber.grab([first, second])
    player.play.assert_called_once()

    grabber._active._on_frame_changed(_frame())
    grabber._active._on_frame_changed(_frame())

    assert [path for path, _image in received] == [str(first), str(second)]
    assert received[0][1].width() == 192
    assert grabber._active is None


def test_late_frame_of_previous_video_is_ignored(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """앞 동영상의 장면이 늦게 도착해도 다음 파일에 붙지 않는지 확인한다."""
    grabber = VideoThumbnailGrabber()
    received: list[str] = []
    grabber.thumbnail_ready.connect(lambda path, _image: received.append(path))
    first, second = tmp_path / "a.mp4", tmp_path / "broken.mov"
    grabber.grab([first, second])
    first_grab = grabber._active

    first_grab._on_frame_changed(_frame())
    first_grab._on_frame_changed(_frame())

    assert received == [str(first)]
    assert grabber._active is not first_grab
    grabber.stop()


def test_grabber_ignores_invalid_frame(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """유효하지 않은 장면은 건너뛰고 계속 기다리는지 확인한다."""
    grabber = VideoThumbnailGrabber()
    received: list[str] = []
    grabber.thumbnail_ready.connect(lambda path, _image: received.append(path))
    grabber.grab([tmp_path / "a.mp4"])
    invalid = MagicMock()
    invalid.isValid.return_value = False

    grabber._active._on_frame_changed(invalid)

    assert received == []
    assert grabber._active is not None
    grabber.stop()


def test_grabber_skips_video_on_error(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """재생할 수 없는 동영상은 건너뛰고 다음으로 넘어가는지 확인한다."""
    player, _sink = grabber_parts
    grabber = VideoThumbnailGrabber()
    grabber.grab([tmp_path / "a.mp4", tmp_path / "b.mp4"])
    first_grab = grabber._active

    first_grab._on_player_error(QMediaPlayer.Error.FormatError, "오류")

    assert grabber._active is not first_grab
    assert player.play.call_count == 2
    grabber.stop()


def test_grabber_skips_video_on_timeout(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """제한 시간이 지나면 다음 동영상으로 넘어가는지 확인한다."""
    with patch("ui.video_thumbnail.GRAB_TIMEOUT_MS", 20):
        grabber = VideoThumbnailGrabber()
        grabber.grab([tmp_path / "a.mp4", tmp_path / "b.mp4"])
        first_grab = grabber._active

    assert _wait_until(lambda: grabber._active is not first_grab)
    grabber.stop()


def test_grabber_uses_new_player_for_each_video(
    app: QApplication, tmp_path: Path
) -> None:
    """파일마다 재생기를 새로 만들고, 이전 재생기를 멈추는지 확인한다."""
    with (
        patch("ui.video_thumbnail.QMediaPlayer") as player_class,
        patch("ui.video_thumbnail.QVideoSink"),
    ):
        players = [MagicMock(), MagicMock()]
        player_class.side_effect = players
        grabber = VideoThumbnailGrabber()

        grabber.grab([tmp_path / "a.mp4", tmp_path / "b.mp4"])
        grabber._active._on_frame_changed(_frame())

        players[0].stop.assert_called_once()
        players[1].play.assert_called_once()
        grabber.stop()


def test_grabber_stop_discards_remaining(
    app: QApplication, grabber_parts: tuple[MagicMock, MagicMock], tmp_path: Path
) -> None:
    """멈추면 남은 작업을 버리고, 늦게 온 장면을 무시하는지 확인한다."""
    grabber = VideoThumbnailGrabber()
    received: list[str] = []
    grabber.thumbnail_ready.connect(lambda path, _image: received.append(path))
    grabber.grab([tmp_path / "a.mp4", tmp_path / "b.mp4"])
    first_grab = grabber._active

    grabber.stop()
    first_grab._on_frame_changed(_frame())

    assert grabber._active is None
    assert received == []
