"""업데이트 테스트. 임시 폴더와 가짜 응답, 이 컴퓨터 안의 서버만 사용한다."""

import io
import json
import os
import stat
import subprocess
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.update_download import fetch_url  # noqa: E402
from core.updater import (  # noqa: E402
    UpdateError,
    apply_command,
    archive_url,
    find_new_version,
    installed_version,
    read_version,
    stage_update,
    take_update_failure,
)
from scripts.apply_update import (  # noqa: E402
    _pip_python,
    install_requirements_if_changed,
    is_process_alive,
    main as apply_main,
    relaunch,
    replace_program_files,
    wait_for_exit,
)
from ui.update_button import UpdateButton  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OLD_VERSION = "a" * 40
NEW_VERSION = "b" * 40


@pytest.fixture(scope="module")
def app() -> QApplication:
    """테스트에 쓸 QApplication을 제공한다."""
    return QApplication.instance() or QApplication([])


def _make_archive(
    version: str = NEW_VERSION,
    top: str = "zipup_helper-new",
    skip: tuple[str, ...] = (),
    extra: dict[str, str] | None = None,
) -> bytes:
    """GitHub 압축 파일과 같은 구조의 가짜 ZIP을 만든다."""
    files = {
        "main.py": "새 버전",
        "requirements.txt": "same",
        "scripts/apply_update.py": "교체",
        "core/app_version.txt": version,
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            if name not in skip:
                archive.writestr(f"{top}/{name}", text)
    return buffer.getvalue()


def _make_installed(root: Path, version: str = OLD_VERSION) -> Path:
    """가짜 설치본을 만든다."""
    (root / "core").mkdir(parents=True)
    (root / "core" / "app_version.txt").write_text(version, encoding="utf-8")
    (root / "core" / "옛모듈.py").write_text("옛", encoding="utf-8")
    (root / "main.py").write_text("옛 버전", encoding="utf-8")
    (root / "requirements.txt").write_text("same", encoding="utf-8")
    return root


def test_read_version_ignores_unfilled_placeholder(tmp_path: Path) -> None:
    """저장소의 원본처럼 커밋 번호가 채워지지 않으면 None인지 확인한다."""
    (tmp_path / "core").mkdir()
    version_file = tmp_path / "core" / "app_version.txt"
    version_file.write_text("$Format:%H$\n", encoding="utf-8")
    assert read_version(tmp_path) is None

    version_file.write_text(f"{OLD_VERSION}\n", encoding="utf-8")
    assert read_version(tmp_path) == OLD_VERSION


def test_repository_version_file_is_filled_by_archive() -> None:
    """ZIP으로 받을 때 커밋 번호가 채워지도록 설정되어 있는지 확인한다."""
    version_text = (ROOT / "core" / "app_version.txt").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert version_text.strip() == "$Format:%H$"
    assert "core/app_version.txt export-subst" in attributes


def test_git_folder_is_never_updated(tmp_path: Path) -> None:
    """개발 중인 Git 폴더는 덮어쓰지 않도록 버전을 돌려주지 않는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    assert installed_version(root) == OLD_VERSION

    (root / ".git").mkdir()
    assert installed_version(root) is None


def test_frozen_program_is_never_updated(tmp_path: Path) -> None:
    """PyInstaller 실행 파일은 이 방식으로 업데이트하지 않는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    with patch.object(sys, "frozen", True, create=True):
        assert installed_version(root) is None


def test_find_new_version() -> None:
    """최신 버전이 다를 때만 그 버전을 돌려주는지 확인한다."""
    fetch = MagicMock(return_value=json.dumps({"sha": NEW_VERSION}).encode())

    assert find_new_version(OLD_VERSION, fetch) == NEW_VERSION
    assert find_new_version(NEW_VERSION, fetch) is None


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b"{}", b'{"sha": 1}', b'{"sha": "../../evil"}']
)
def test_find_new_version_rejects_bad_response(body: bytes) -> None:
    """이상한 응답은 버전으로 받아들이지 않는지 확인한다."""
    with pytest.raises(UpdateError):
        find_new_version(OLD_VERSION, lambda _url: body)


def test_stage_update_prepares_without_touching_program(tmp_path: Path) -> None:
    """준비 단계에서는 프로그램 파일을 바꾸지 않는지 확인한다."""
    root = _make_installed(tmp_path / "설치")

    staged = stage_update(_make_archive(), NEW_VERSION, root)

    assert staged == root / ".update" / "new"
    assert (staged / "main.py").read_text(encoding="utf-8") == "새 버전"
    assert (root / ".update" / "apply_update.py").is_file()
    assert (root / "main.py").read_text(encoding="utf-8") == "옛 버전"


@pytest.mark.parametrize(
    "data",
    [
        "zip 아님".encode(),
        _make_archive(version=OLD_VERSION),
        _make_archive(version="$Format:%H$"),
        _make_archive(skip=("main.py",)),
    ],
)
def test_stage_update_rejects_bad_archive(tmp_path: Path, data: bytes) -> None:
    """손상되었거나 버전이 다른 파일은 거부하는지 확인한다."""
    root = _make_installed(tmp_path / "설치")

    with pytest.raises(UpdateError):
        stage_update(data, NEW_VERSION, root)

    assert (root / "main.py").read_text(encoding="utf-8") == "옛 버전"


def test_stage_update_rejects_several_top_folders(tmp_path: Path) -> None:
    """최상위 폴더가 여러 개인 파일을 거부하는지 확인한다."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a/main.py", "")
        archive.writestr("b/main.py", "")

    with pytest.raises(UpdateError):
        stage_update(buffer.getvalue(), NEW_VERSION, _make_installed(tmp_path / "설치"))


def test_stage_update_does_not_write_outside_program(tmp_path: Path) -> None:
    """압축 파일이 상위 경로를 가리켜도 프로그램 폴더 밖에 쓰지 않는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    data = _make_archive(extra={"../../../밖.txt": "침범"})

    stage_update(data, NEW_VERSION, root)

    found = list(tmp_path.rglob("밖.txt"))
    assert all(root in path.parents for path in found)


@pytest.mark.skipif(os.name == "nt", reason="실행 권한은 Windows에서 확인할 수 없다")
def test_stage_update_keeps_executable_permission(tmp_path: Path) -> None:
    """Mac 설치 파일의 실행 권한이 압축을 풀어도 유지되는지 확인한다."""
    buffer = io.BytesIO(_make_archive())
    with zipfile.ZipFile(buffer, "a") as archive:
        info = zipfile.ZipInfo("zipup_helper-new/install_mac.command")
        info.external_attr = 0o100755 << 16
        archive.writestr(info, "#!/bin/bash")

    staged = stage_update(buffer.getvalue(), NEW_VERSION, _make_installed(tmp_path / "설치"))

    assert (staged / "install_mac.command").stat().st_mode & stat.S_IXUSR


def test_apply_command_runs_copied_script(tmp_path: Path) -> None:
    """교체 폴더 밖으로 복사한 스크립트를 실행하는지 확인한다."""
    program, arguments = apply_command(tmp_path / ".update" / "new", 123, tmp_path)

    assert program == sys.executable
    assert arguments == [
        str(tmp_path / ".update" / "apply_update.py"),
        str(tmp_path),
        str(tmp_path / ".update" / "new"),
        "123",
    ]


def test_take_update_failure_reports_once(tmp_path: Path) -> None:
    """실패 기록을 한 번만 알리는지 확인한다."""
    assert take_update_failure(tmp_path) is False
    (tmp_path / ".update").mkdir()
    (tmp_path / ".update" / "failed.txt").touch()

    assert take_update_failure(tmp_path) is True
    assert take_update_failure(tmp_path) is False


def _staged_root(tmp_path: Path) -> tuple[Path, Path]:
    """보호 항목이 있는 설치본과 준비 폴더를 만든다."""
    root = _make_installed(tmp_path / "설치")
    for name in (".venv", "outputs"):
        (root / name).mkdir()
        (root / name / "기존.txt").write_text("보존", encoding="utf-8")
    (root / ".env").write_text("보존", encoding="utf-8")
    staged = stage_update(
        _make_archive(
            extra={"outputs/.gitkeep": "", ".env": "덮어쓰기", "ui/새모듈.py": "새"}
        ),
        NEW_VERSION,
        root,
    )
    return root, staged


def test_replace_program_files_keeps_user_files(tmp_path: Path) -> None:
    """프로그램은 바뀌고 가상환경, 설정, 결과물은 그대로인지 확인한다."""
    root, staged = _staged_root(tmp_path)

    replace_program_files(root, staged, root / ".update" / "backup")

    assert (root / "main.py").read_text(encoding="utf-8") == "새 버전"
    assert read_version(root) == NEW_VERSION
    assert (root / "ui" / "새모듈.py").is_file()
    assert not (root / "core" / "옛모듈.py").exists()
    assert (root / ".venv" / "기존.txt").is_file()
    assert (root / "outputs" / "기존.txt").is_file()
    assert (root / ".env").read_text(encoding="utf-8") == "보존"


def test_replace_program_files_rolls_back_on_failure(tmp_path: Path) -> None:
    """교체 중 실패하면 이전 파일로 모두 되돌리는지 확인한다."""
    root, staged = _staged_root(tmp_path)
    real_replace = os.replace
    calls = {"count": 0}

    def fail_later(source: Path, target: Path) -> None:
        """몇 번 성공한 뒤 실패한다."""
        calls["count"] += 1
        if calls["count"] == 4:
            raise OSError("실패")
        real_replace(source, target)

    with patch("scripts.apply_update.os.replace", fail_later), pytest.raises(OSError):
        replace_program_files(root, staged, root / ".update" / "backup")

    assert (root / "main.py").read_text(encoding="utf-8") == "옛 버전"
    assert read_version(root) == OLD_VERSION
    assert (root / "core" / "옛모듈.py").is_file()
    assert not (root / "ui").exists()


def test_requirements_installed_only_when_changed(tmp_path: Path) -> None:
    """패키지 목록이 바뀔 때만 설치하는지 확인한다."""
    root, staged = _staged_root(tmp_path)
    run = MagicMock()

    assert install_requirements_if_changed(root, staged, Path("python"), run) is False
    run.assert_not_called()

    (staged / "requirements.txt").write_text("changed", encoding="utf-8")
    assert install_requirements_if_changed(root, staged, Path("python"), run) is True
    command = run.call_args.args[0]
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[-1] == str(staged / "requirements.txt")
    assert run.call_args.kwargs["check"] is True


def test_pip_python_avoids_pythonw() -> None:
    """Windows의 pythonw 대신 python으로 pip를 실행하는지 확인한다."""
    assert _pip_python(Path("Scripts/pythonw.exe")).name == "python.exe"
    assert _pip_python(Path("bin/python")).name == "python"


def test_wait_for_exit() -> None:
    """프로세스가 끝나면 True, 시간이 지나면 False를 반환하는지 확인한다."""
    states = iter([True, True, False])
    assert wait_for_exit(1, 10, lambda _pid: next(states), lambda _s: None) is True
    assert wait_for_exit(1, 1, lambda _pid: True, lambda _s: None) is False


def test_is_process_alive_with_real_process() -> None:
    """실제 프로세스의 상태를 바르게 읽고, 확인만으로 끝내지 않는지 확인한다."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
    )
    try:
        assert is_process_alive(process.pid) is True
        assert process.poll() is None
    finally:
        process.communicate(b"")
    assert is_process_alive(process.pid) is False


def test_relaunch_runs_main_detached(tmp_path: Path) -> None:
    """프로그램 폴더에서 main.py를 다시 켜는지 확인한다."""
    popen = MagicMock()

    relaunch(tmp_path, Path("python"), popen)

    assert popen.call_args.args[0] == ["python", str(tmp_path / "main.py")]
    assert popen.call_args.kwargs["cwd"] == tmp_path


def test_apply_main_updates_and_relaunches(tmp_path: Path) -> None:
    """프로그램이 꺼진 뒤 교체하고 다시 켜며, 준비 폴더를 지우는지 확인한다."""
    root, staged = _staged_root(tmp_path)

    with (
        patch("scripts.apply_update.wait_for_exit", return_value=True),
        patch("scripts.apply_update.relaunch") as relaunch_mock,
        patch("scripts.apply_update.logging.basicConfig"),
    ):
        result = apply_main([str(root), str(staged), "1"])

    assert result == 0
    assert read_version(root) == NEW_VERSION
    assert not staged.exists()
    assert not (root / ".update" / "failed.txt").exists()
    relaunch_mock.assert_called_once()


def test_apply_main_relaunches_old_version_on_failure(tmp_path: Path) -> None:
    """교체에 실패하면 실패를 기록하고 이전 버전으로 다시 켜는지 확인한다."""
    root, staged = _staged_root(tmp_path)

    with (
        patch("scripts.apply_update.wait_for_exit", return_value=True),
        patch("scripts.apply_update.relaunch") as relaunch_mock,
        patch("scripts.apply_update.logging.basicConfig"),
        patch("scripts.apply_update.logging.exception"),
        patch("scripts.apply_update.replace_program_files", side_effect=OSError("실패")),
    ):
        result = apply_main([str(root), str(staged), "1"])

    assert result == 1
    assert read_version(root) == OLD_VERSION
    assert (root / ".update" / "failed.txt").is_file()
    relaunch_mock.assert_called_once()


def test_apply_main_does_nothing_while_program_runs(tmp_path: Path) -> None:
    """프로그램이 꺼지지 않으면 교체하지 않고 새로 켜지도 않는지 확인한다."""
    root, staged = _staged_root(tmp_path)

    with (
        patch("scripts.apply_update.wait_for_exit", return_value=False),
        patch("scripts.apply_update.relaunch") as relaunch_mock,
        patch("scripts.apply_update.logging.basicConfig"),
        patch("scripts.apply_update.logging.error"),
    ):
        result = apply_main([str(root), str(staged), "1"])

    assert result == 1
    assert read_version(root) == OLD_VERSION
    relaunch_mock.assert_not_called()


class _Handler(BaseHTTPRequestHandler):
    """정해진 응답을 돌려주는 이 컴퓨터 안의 서버."""

    def do_GET(self) -> None:
        """주소에 따라 응답한다."""
        if self.path == "/ok":
            body = b"content:" + self.headers["User-Agent"].encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            threading.Event().wait(1)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        """테스트 출력에 로그를 남기지 않는다."""


@pytest.fixture
def server() -> Iterator[str]:
    """이 컴퓨터 안의 서버를 띄우고 주소를 제공한다."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def test_fetch_url_returns_content(app: QApplication, server: str) -> None:
    """내용을 받고 User-Agent를 보내는지 확인한다."""
    assert fetch_url(f"{server}/ok") == b"content:zipup_helper"


def test_fetch_url_raises_on_http_error(app: QApplication, server: str) -> None:
    """없는 주소는 오류로 처리하는지 확인한다."""
    with pytest.raises(UpdateError):
        fetch_url(f"{server}/missing")


def test_fetch_url_stops_when_requested(app: QApplication, server: str) -> None:
    """중단을 요청하면 오류로 끝나는지 확인한다."""
    with pytest.raises(UpdateError):
        fetch_url(f"{server}/slow", should_stop=lambda: True)


def _wait_until(condition: object, timeout_ms: int = 5000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다린다."""
    from PySide6.QtTest import QTest

    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20
    return bool(condition())


def test_button_hidden_for_git_folder(app: QApplication, tmp_path: Path) -> None:
    """Git 폴더에서는 확인도 하지 않고 버튼도 나오지 않는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    (root / ".git").mkdir()
    button = UpdateButton(root)

    with patch("ui.update_worker.fetch_url") as fetch:
        button.check_for_update()

    assert button._worker is None
    fetch.assert_not_called()
    assert button.isHidden()


def test_button_appears_only_for_new_version(app: QApplication, tmp_path: Path) -> None:
    """새 버전이 있을 때만 버튼이 나타나는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    response = json.dumps({"sha": NEW_VERSION}).encode()

    button = UpdateButton(root)
    with patch("ui.update_worker.fetch_url", return_value=response):
        button.check_for_update()
        assert _wait_until(lambda: button._worker is None)
    assert not button.isHidden()

    same = UpdateButton(root)
    with patch(
        "ui.update_worker.fetch_url",
        return_value=json.dumps({"sha": OLD_VERSION}).encode(),
    ):
        same.check_for_update()
        assert _wait_until(lambda: same._worker is None)
    assert same.isHidden()


def test_button_stays_hidden_without_internet(app: QApplication, tmp_path: Path) -> None:
    """인터넷이 없어도 조용히 넘어가는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    button = UpdateButton(root)

    with (
        patch("ui.update_worker.fetch_url", side_effect=UpdateError("없음")),
        patch("ui.update_button.warn") as warn_mock,
    ):
        button.check_for_update()
        assert _wait_until(lambda: button._worker is None)

    assert button.isHidden()
    warn_mock.assert_not_called()


def test_button_warns_about_last_failure(app: QApplication, tmp_path: Path) -> None:
    """지난 업데이트 실패를 켤 때 알리는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    (root / ".git").mkdir()
    (root / ".update").mkdir()
    (root / ".update" / "failed.txt").touch()

    with patch("ui.update_button.warn") as warn_mock:
        UpdateButton(root).check_for_update()

    warn_mock.assert_called_once()


def test_click_cancel_does_nothing(app: QApplication, tmp_path: Path) -> None:
    """확인창에서 아니오를 누르면 내려받지 않는지 확인한다."""
    button = UpdateButton(_make_installed(tmp_path / "설치"))
    button._version = NEW_VERSION

    with (
        patch("ui.update_button.confirm", return_value=False),
        patch("ui.update_worker.fetch_url") as fetch,
    ):
        button.click()

    assert button._worker is None
    fetch.assert_not_called()


def test_click_downloads_then_starts_replacer_and_closes(
    app: QApplication, tmp_path: Path
) -> None:
    """누르면 내려받아 준비하고, 교체 프로그램을 띄운 뒤 창을 닫는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    button = UpdateButton(root)
    button._version = NEW_VERSION
    button.show()

    with (
        patch("ui.update_button.confirm", return_value=True),
        patch("ui.update_worker.fetch_url", return_value=_make_archive()) as fetch,
        patch(
            "ui.update_button.QProcess.startDetached", return_value=(True, 1)
        ) as start,
    ):
        button.click()
        assert not button.isEnabled()
        assert _wait_until(lambda: button._worker is None)

    assert fetch.call_args.args[0] == archive_url(NEW_VERSION)
    program, arguments, working_folder = start.call_args.args
    assert program == sys.executable
    assert arguments[-1] == str(QCoreApplication.applicationPid())
    assert working_folder == str(root / ".update")
    assert not button.isVisible()
    assert (root / "main.py").read_text(encoding="utf-8") == "옛 버전"


@pytest.mark.parametrize(
    "fetch_options",
    [
        {"side_effect": UpdateError("인터넷 끊김")},
        {"return_value": b"not zip"},
    ],
)
def test_download_failure_warns_and_allows_retry(
    app: QApplication, tmp_path: Path, fetch_options: dict[str, object]
) -> None:
    """내려받기에 실패하면 알리고, 다시 누를 수 있으며 프로그램은 그대로인지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    button = UpdateButton(root)
    button._version = NEW_VERSION

    with (
        patch("ui.update_button.confirm", return_value=True),
        patch("ui.update_worker.fetch_url", **fetch_options),
        patch("ui.update_button.warn") as warn_mock,
        patch("ui.update_button.QProcess.startDetached") as start,
    ):
        button.click()
        assert _wait_until(lambda: button._worker is None)

    warn_mock.assert_called_once()
    start.assert_not_called()
    assert button.isEnabled()
    assert button.text() == "업데이트"
    assert (root / "main.py").read_text(encoding="utf-8") == "옛 버전"


def test_failed_replacer_start_warns(app: QApplication, tmp_path: Path) -> None:
    """교체 프로그램을 띄우지 못하면 창을 닫지 않고 알리는지 확인한다."""
    root = _make_installed(tmp_path / "설치")
    button = UpdateButton(root)
    button._version = NEW_VERSION
    button.show()

    with (
        patch("ui.update_button.confirm", return_value=True),
        patch("ui.update_worker.fetch_url", return_value=_make_archive()),
        patch("ui.update_button.QProcess.startDetached", return_value=(False, 0)),
        patch("ui.update_button.warn") as warn_mock,
    ):
        button.click()
        assert _wait_until(lambda: button._worker is None)

    warn_mock.assert_called_once()
    assert button.isVisible()
    button.close()
