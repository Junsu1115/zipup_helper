"""PPTX 저장과 드래그 앤 드롭 테스트. 임시 폴더와 가짜 PPTX만 사용한다."""

import os
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.pptx_import import (
    classify_dropped_files,
    import_pptx,
    import_pptx_files,
)
from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.project_selector import ProjectSelector
from ui.toast import _Toast


def _make_pptx(
    path: Path, media: dict[str, bytes] | None = None, extra: dict[str, bytes] | None = None
) -> Path:
    """media 파일이 들어 있는 가짜 PPTX를 만든다."""
    if media is None:
        media = {"image1.png": b"PNG1", "image2.jpg": b"JPG2"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("ppt/slides/slide1.xml", b"<slide/>")
        for name, content in media.items():
            archive.writestr(f"ppt/media/{name}", content)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """빈 프로젝트 폴더를 만들어 반환한다."""
    project_path = tmp_path / "test"
    project_path.mkdir()
    return project_path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """끌어다 놓을 원본 PPTX를 만들어 반환한다."""
    return _make_pptx(tmp_path / "제안서.pptx")


def _names(folder: Path) -> set[str]:
    """폴더 안의 항목 이름을 반환한다."""
    return {entry.name for entry in folder.iterdir()}


def test_import_saves_original_and_only_media(project: Path, source: Path) -> None:
    """원본은 PPT_ORG에, media 폴더만 PPT_Break에 저장되는지 확인한다."""
    assert import_pptx(source, project) == 2

    document = project / "Document"
    assert _names(document / "PPT_ORG") == {"제안서.pptx"}
    assert _names(document / "PPT_Break") == {"media"}
    media = document / "PPT_Break" / "media"
    assert _names(media) == {"image1.png", "image2.jpg"}
    assert (media / "image1.png").read_bytes() == b"PNG1"


def test_import_keeps_dropped_file_and_copy_identical(
    project: Path, source: Path
) -> None:
    """끌어다 놓은 원본이 그대로 남고, 복사본의 확장자와 내용이 같은지 확인한다."""
    original_bytes = source.read_bytes()

    import_pptx(source, project)

    assert source.read_bytes() == original_bytes
    copied = project / "Document" / "PPT_ORG" / "제안서.pptx"
    assert copied.read_bytes() == original_bytes


def test_import_uses_existing_folders_and_keeps_contents(
    project: Path, source: Path
) -> None:
    """이미 있는 폴더를 그대로 쓰고, 안의 파일을 건드리지 않는지 확인한다."""
    original_folder = project / "Document" / "PPT_ORG"
    original_folder.mkdir(parents=True)
    (original_folder / "기존.pptx").write_bytes(b"old")

    import_pptx(source, project)

    assert _names(original_folder) == {"기존.pptx", "제안서.pptx"}
    assert (original_folder / "기존.pptx").read_bytes() == b"old"


def test_same_name_original_is_not_overwritten(
    project: Path, source: Path
) -> None:
    """같은 이름의 원본이 있으면 덮어쓰지 않고 오류를 내는지 확인한다."""
    original = project / "Document" / "PPT_ORG" / "제안서.pptx"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"old")

    summary = import_pptx_files([source], project)

    assert original.read_bytes() == b"old"
    assert summary.imported_count == 0
    assert "같은 이름의 파일" in summary.failures[0]
    assert not (project / "Document" / "PPT_Break" / "media").exists()


def test_next_pptx_goes_to_numbered_media_folder(
    project: Path, source: Path, tmp_path: Path
) -> None:
    """PPT를 넣을 때마다 media2, media3에 꺼내고 앞의 폴더는 그대로인지 확인한다."""
    second = _make_pptx(tmp_path / "회사소개.pptx", {"image1.png": b"OTHER"})
    third = _make_pptx(tmp_path / "시안.pptx", {"image1.png": b"THIRD"})

    summary = import_pptx_files([source, second, third], project)

    document = project / "Document"
    assert summary.imported_count == 3
    assert summary.media_count == 4
    assert summary.failures == []
    assert _names(document / "PPT_ORG") == {"제안서.pptx", "회사소개.pptx", "시안.pptx"}
    breaks = document / "PPT_Break"
    assert _names(breaks) == {"media", "media2", "media3"}
    assert (breaks / "media" / "image1.png").read_bytes() == b"PNG1"
    assert (breaks / "media2" / "image1.png").read_bytes() == b"OTHER"
    assert (breaks / "media3" / "image1.png").read_bytes() == b"THIRD"


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (["media", "media3"], "media4"),
        (["media3"], "media4"),
        (["media_old", "media시안", "media2a", "old_media5"], "media"),
        (["media", "media_old"], "media2"),
        (["media", "media9", "media10"], "media11"),
    ],
)
def test_media_folder_number_follows_highest(
    project: Path, source: Path, existing: list[str], expected: str
) -> None:
    """가장 큰 번호 다음을 쓰고, 뒤에 숫자만 있는 이름만 번호로 세는지 확인한다."""
    breaks = project / "Document" / "PPT_Break"
    for name in existing:
        (breaks / name).mkdir(parents=True)

    import_pptx_files([source], project)

    assert _names(breaks) == {*existing, expected}
    assert _names(breaks / expected) == {"image1.png", "image2.jpg"}


def test_media_number_ignores_letter_case(project: Path, source: Path) -> None:
    """대소문자만 다른 이름도 번호로 세어 이름이 겹치지 않는지 확인한다."""
    breaks = project / "Document" / "PPT_Break"
    (breaks / "MEDIA2").mkdir(parents=True)

    import_pptx_files([source], project)

    assert (breaks / "media3" / "image1.png").exists()


def test_file_named_like_media_folder_is_counted(project: Path, source: Path) -> None:
    """같은 이름의 일반 파일이 있어도 폴더 만들기가 실패하지 않는지 확인한다."""
    breaks = project / "Document" / "PPT_Break"
    breaks.mkdir(parents=True)
    (breaks / "media").write_bytes(b"file")

    summary = import_pptx_files([source], project)

    assert summary.failures == []
    assert (breaks / "media2" / "image1.png").exists()


def test_pptx_without_media_does_not_use_number(
    project: Path, source: Path, tmp_path: Path
) -> None:
    """사진이 없는 PPT는 번호를 쓰지 않아 다음 PPT가 media2를 쓰는지 확인한다."""
    empty = _make_pptx(tmp_path / "빈.pptx", media={})
    second = _make_pptx(tmp_path / "회사소개.pptx")

    import_pptx_files([source, empty, second], project)

    assert _names(project / "Document" / "PPT_Break") == {"media", "media2"}


def test_pptx_without_media_saves_original_only(
    project: Path, tmp_path: Path
) -> None:
    """사진이 없으면 원본만 저장하고 media 폴더를 만들지 않는지 확인한다."""
    source = _make_pptx(tmp_path / "빈.pptx", media={})

    assert import_pptx(source, project) == 0

    document = project / "Document"
    assert _names(document / "PPT_ORG") == {"빈.pptx"}
    assert _names(document / "PPT_Break") == set()


def test_broken_pptx_keeps_original_and_reports(
    project: Path, tmp_path: Path
) -> None:
    """손상된 파일은 원본을 남기고 실패로 알리는지 확인한다."""
    broken = tmp_path / "손상.pptx"
    broken.write_bytes(b"not a zip")

    summary = import_pptx_files([broken], project)

    assert (project / "Document" / "PPT_ORG" / "손상.pptx").exists()
    assert summary.imported_count == 0
    assert "손상" in summary.failures[0]


def test_one_failure_does_not_stop_other_files(
    project: Path, source: Path, tmp_path: Path
) -> None:
    """한 파일이 실패해도 다음 파일을 처리하는지 확인한다."""
    broken = tmp_path / "손상.pptx"
    broken.write_bytes(b"not a zip")

    summary = import_pptx_files([broken, source], project)

    assert summary.imported_count == 1
    assert len(summary.failures) == 1
    assert (project / "Document" / "PPT_Break" / "media" / "image1.png").exists()


def test_malicious_paths_stay_inside_media_folder(
    project: Path, tmp_path: Path
) -> None:
    """압축 파일 안의 위험한 경로가 media 폴더 밖에 쓰이지 않는지 확인한다."""
    source = _make_pptx(
        tmp_path / "위험.pptx",
        media={},
        extra={
            "ppt/media/../../../../../evil.png": b"evil",
            "ppt/media/..\\..\\evil2.png": b"evil",
            "ppt/media/sub/image.png": b"ok",
            "ppt/slides/evil3.png": b"not media",
        },
    )

    import_pptx(source, project)

    media = project / "Document" / "PPT_Break" / "media"
    assert _names(media) == {"evil.png", "evil2.png", "image.png"}
    assert not (tmp_path / "evil.png").exists()
    assert not (project / "evil.png").exists()
    assert not (project / "Document" / "evil.png").exists()


def test_classify_splits_by_extension(tmp_path: Path) -> None:
    """확장자에 따라 파일이 나뉘고, 대문자 확장자도 인식하는지 확인한다."""
    pptx = tmp_path / "A.PPTX"
    pptx.touch()
    legacy = tmp_path / "b.ppt"
    legacy.touch()
    other = tmp_path / "c.png"
    other.touch()

    dropped = classify_dropped_files([pptx, legacy, other])

    assert dropped.pptx == [pptx]
    assert dropped.legacy_ppt == [legacy]
    assert dropped.unsupported == [other]


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


@pytest.fixture
def window(app: QApplication, tmp_path: Path) -> Iterator[MainWindow]:
    """임시 폴더의 test 프로젝트가 선택된 메인 창을 제공한다."""
    root = tmp_path / "root"
    (root / "test").mkdir(parents=True)
    with patch(
        "ui.main_window.ProjectSelector", lambda: ProjectSelector(root)
    ):
        main_window = MainWindow()
    main_window.show()
    yield main_window
    main_window.close()


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다리고, 조건의 결과를 반환한다."""
    waited_ms = 0
    while not condition() and waited_ms < timeout_ms:
        QTest.qWait(20)
        waited_ms += 20
    return condition()


def _mime_data(paths: list[Path]) -> QMimeData:
    """파일 경로를 담은 드래그 데이터를 만든다."""
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime_data


def _drop(
    window: MainWindow, paths: list[Path], answer: bool = True
) -> tuple[MagicMock, MagicMock]:
    """파일을 놓고 처리가 끝나기를 기다린 뒤, 확인창과 경고창 호출 기록을 반환한다."""
    mime_data = _mime_data(paths)
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime_data,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    with (
        patch("ui.main_window.confirm", return_value=answer) as confirm_mock,
        patch("ui.main_window.warn") as warn_mock,
    ):
        window.dropEvent(event)
        QApplication.processEvents()
        assert _wait_until(lambda: window._import_worker is None)
        QTest.qWait(20)
    return confirm_mock, warn_mock


def test_drag_enter_accepts_only_local_files(
    window: MainWindow, source: Path
) -> None:
    """이 컴퓨터의 파일만 끌어다 놓을 수 있는지 확인한다."""
    def enter_event(mime_data: QMimeData) -> QDragEnterEvent:
        """드래그 진입 이벤트를 만든다."""
        return QDragEnterEvent(
            QPointF(10, 10).toPoint(),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    # 이벤트는 드래그 데이터를 소유하지 않으므로 변수에 담아 살려 둔다.
    local_data = _mime_data([source])
    local_event = enter_event(local_data)
    local_event.ignore()
    window.dragEnterEvent(local_event)
    assert local_event.isAccepted()

    remote_data = QMimeData()
    remote_data.setUrls([QUrl("https://example.com/a.pptx")])
    remote_event = enter_event(remote_data)
    remote_event.ignore()
    window.dragEnterEvent(remote_event)
    assert not remote_event.isAccepted()


def test_drop_asks_with_project_name_and_saves(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """프로젝트 이름으로 묻고, 예를 누르면 저장 후 알림을 표시하는지 확인한다."""
    confirm_mock, warn_mock = _drop(window, [source])

    confirm_mock.assert_called_once()
    assert confirm_mock.call_args.args[2] == "'test' 프로젝트에 PPT를 저장하시겠습니까?"
    warn_mock.assert_not_called()
    document = tmp_path / "root" / "test" / "Document"
    assert _names(document / "PPT_ORG") == {"제안서.pptx"}
    assert _names(document / "PPT_Break" / "media") == {"image1.png", "image2.jpg"}
    assert window.findChild(_Toast).text() == "사진 2개를 저장했습니다"


def test_answering_no_saves_nothing(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """아니오를 누르면 아무것도 저장하지 않는지 확인한다."""
    _drop(window, [source], answer=False)

    assert _names(tmp_path / "root" / "test") == set()


def test_multiple_files_show_count_and_save_to_numbered_folders(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """여러 개를 놓으면 개수를 표시하고, 경고 없이 번호 폴더에 꺼내는지 확인한다."""
    second = _make_pptx(tmp_path / "회사소개.pptx")

    confirm_mock, warn_mock = _drop(window, [source, second])

    assert confirm_mock.call_args.args[2] == "'test' 프로젝트에 PPT 2개를 저장하시겠습니까?"
    warn_mock.assert_not_called()
    document = tmp_path / "root" / "test" / "Document"
    assert _names(document / "PPT_ORG") == {"제안서.pptx", "회사소개.pptx"}
    assert _names(document / "PPT_Break") == {"media", "media2"}


def test_legacy_ppt_only_shows_guidance_without_confirm(
    window: MainWindow, tmp_path: Path
) -> None:
    """구형 PPT만 놓으면 묻지 않고 PPTX 저장을 안내하는지 확인한다."""
    legacy = tmp_path / "옛날.ppt"
    legacy.write_bytes(b"old")

    confirm_mock, warn_mock = _drop(window, [legacy])

    confirm_mock.assert_not_called()
    assert "PPTX로 저장한 뒤" in warn_mock.call_args.args[2]
    assert _names(tmp_path / "root" / "test") == set()


def test_mixed_drop_saves_pptx_and_reports_skipped_files(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """PPTX와 다른 파일을 함께 놓으면 PPTX만 저장하고 나머지를 알리는지 확인한다."""
    legacy = tmp_path / "옛날.ppt"
    legacy.write_bytes(b"old")
    image = tmp_path / "사진.png"
    image.write_bytes(b"png")

    confirm_mock, warn_mock = _drop(window, [source, legacy, image])

    assert confirm_mock.call_args.args[2] == "'test' 프로젝트에 PPT를 저장하시겠습니까?"
    message = warn_mock.call_args.args[2]
    assert "옛날.ppt" in message
    assert "사진.png" in message
    assert _names(tmp_path / "root" / "test" / "Document" / "PPT_ORG") == {
        "제안서.pptx"
    }


def test_drop_without_project_warns(
    app: QApplication, source: Path, tmp_path: Path
) -> None:
    """선택한 프로젝트가 없으면 경고만 띄우는지 확인한다."""
    root = tmp_path / "빈루트"
    root.mkdir()
    with patch(
        "ui.main_window.ProjectSelector", lambda: ProjectSelector(root)
    ):
        main_window = MainWindow()

    confirm_mock, warn_mock = _drop(main_window, [source])

    confirm_mock.assert_not_called()
    warn_mock.assert_called_once()
    assert _names(root) == set()


def test_drop_saves_to_project_selected_when_dropped(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """작업이 끝난 뒤 프로젝트를 바꿔도 놓은 시점의 프로젝트에 저장되는지 확인한다."""
    root = tmp_path / "root"
    (root / "다른프로젝트").mkdir()
    assert _wait_until(lambda: window._selector._combo_box.count() == 2)
    window._selector._combo_box.setCurrentText("test")

    _drop(window, [source])
    window._selector._combo_box.setCurrentText("다른프로젝트")

    assert (root / "test" / "Document" / "PPT_ORG" / "제안서.pptx").exists()
    assert _names(root / "다른프로젝트") == set()


def test_drop_during_import_warns(
    window: MainWindow, source: Path, tmp_path: Path
) -> None:
    """저장 중에 또 놓으면 경고하고 새 작업을 시작하지 않는지 확인한다."""
    window._import_worker = MagicMock()
    with (
        patch("ui.main_window.confirm") as confirm_mock,
        patch("ui.main_window.warn") as warn_mock,
    ):
        window._handle_dropped_paths([source])
    window._import_worker = None

    confirm_mock.assert_not_called()
    warn_mock.assert_called_once()
    assert _names(tmp_path / "root" / "test") == set()
