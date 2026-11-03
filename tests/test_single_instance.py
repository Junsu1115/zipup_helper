"""ui.single_instance와 main의 중복 실행 방지 테스트."""

import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import main as main_module
from ui.single_instance import SingleInstance, bring_to_front

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESS_TIMEOUT_SECONDS = 30

NOTIFY_TIMEOUT_MS = 10000
ACTIVATED_MARKER = "activated"

# 잠금을 잡고 알림을 기다리다, 알림이 오면 표시 파일을 만드는 프로그램. 끄는 일은 테스트가 한다.
_HOLDER_SCRIPT = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication
from ui.single_instance import SingleInstance

app = QCoreApplication([])
folder = Path(sys.argv[1])
instance = SingleInstance(folder, sys.argv[2])
assert instance.acquire()
instance.activation_requested.connect(lambda: (folder / sys.argv[3]).touch())
print("ready", flush=True)
app.exec()
"""


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_instance(tmp_path: Path) -> Iterator[Callable[[], SingleInstance]]:
    """같은 잠금을 쓰는 SingleInstance를 만들고, 끝나면 모두 해제한다."""
    name = f"zipup_test_{uuid.uuid4().hex[:12]}"
    created: list[SingleInstance] = []

    def create() -> SingleInstance:
        """새 SingleInstance를 만들어 반환한다."""
        instance = SingleInstance(tmp_path, name)
        created.append(instance)
        return instance

    yield create
    for instance in created:
        instance.release()


def _wait_until(condition: Callable[[], bool], timeout_ms: int = 3000) -> bool:
    """조건이 참이 될 때까지 이벤트를 처리하며 기다린다."""
    waited = 0
    while not condition() and waited < timeout_ms:
        QTest.qWait(20)
        waited += 20
    return condition()


def _start_holder(folder: Path, name: str) -> subprocess.Popen[str]:
    """잠금을 잡은 별도 프로세스를 띄우고, 준비될 때까지 기다린다."""
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(folder), name, ACTIVATED_MARKER],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def test_first_acquire_succeeds_and_second_fails(
    app: QApplication, make_instance: Callable[[], SingleInstance]
) -> None:
    """먼저 잡으면 성공하고, 잠겨 있으면 실패하는지 확인한다."""
    assert make_instance().acquire()
    assert not make_instance().acquire()


def test_acquire_succeeds_after_release(
    app: QApplication, make_instance: Callable[[], SingleInstance]
) -> None:
    """정상 종료로 잠금을 풀면 다시 켤 수 있는지 확인한다."""
    first = make_instance()
    assert first.acquire()
    first.release()

    assert make_instance().acquire()


def test_notify_reaches_running_instance(
    app: QApplication, make_instance: Callable[[], SingleInstance]
) -> None:
    """두 번째 실행의 알림이 먼저 켜진 쪽에 신호로 전달되는지 확인한다."""
    first = make_instance()
    assert first.acquire()
    received = MagicMock()
    first.activation_requested.connect(received)

    assert make_instance().notify_running()

    assert _wait_until(lambda: received.call_count == 1)


def test_notify_fails_when_nothing_is_running(
    app: QApplication, make_instance: Callable[[], SingleInstance]
) -> None:
    """켜진 프로그램이 없으면 알림이 실패로 끝나는지 확인한다."""
    assert not make_instance().notify_running()


def test_unusable_lock_folder_still_lets_program_start(
    app: QApplication, tmp_path: Path
) -> None:
    """잠금 파일을 못 만들어도 프로그램은 켜지는지 확인한다."""
    instance = SingleInstance(tmp_path / "없는 폴더", "zipup_test_unusable")

    assert instance.acquire()
    instance.release()


def test_other_process_is_blocked_and_notified(
    app: QApplication, tmp_path: Path
) -> None:
    """실제로 다른 프로세스가 켜져 있으면 막히고, 알림이 전달되는지 확인한다."""
    name = f"zipup_test_{uuid.uuid4().hex[:12]}"
    holder = _start_holder(tmp_path, name)
    second = SingleInstance(tmp_path, name)
    try:
        assert not second.acquire()
        assert second.notify_running()
        marker = tmp_path / ACTIVATED_MARKER
        assert _wait_until(marker.exists, NOTIFY_TIMEOUT_MS)
    finally:
        holder.kill()
        holder.wait(timeout=PROCESS_TIMEOUT_SECONDS)
        second.release()


def test_lock_left_by_killed_process_is_cleared(
    app: QApplication, tmp_path: Path
) -> None:
    """강제 종료로 잠금 파일이 남아도 다시 켤 수 있는지 확인한다. 업데이트 재시작도 같은 경우다."""
    name = f"zipup_test_{uuid.uuid4().hex[:12]}"
    holder = _start_holder(tmp_path, name)
    holder.kill()
    holder.communicate(timeout=PROCESS_TIMEOUT_SECONDS)
    assert (tmp_path / f"{name}.lock").exists()
    next_run = SingleInstance(tmp_path, name)

    assert next_run.acquire()

    received = MagicMock()
    next_run.activation_requested.connect(received)
    assert SingleInstance(tmp_path, name).notify_running()
    assert _wait_until(lambda: received.call_count == 1)
    next_run.release()


def test_bring_to_front_restores_minimized_window(app: QApplication) -> None:
    """최소화된 창이 원래 상태로 돌아오는지 확인한다."""
    window = QWidget()
    window.show()
    window.setWindowState(Qt.WindowState.WindowMinimized)

    bring_to_front(window)

    assert not window.isMinimized()
    assert window.isVisible()
    window.close()


def _patch_main(
    monkeypatch: pytest.MonkeyPatch,
    app: QApplication,
    instance: SingleInstance,
) -> MagicMock:
    """main이 실제 창과 이벤트 루프를 쓰지 않도록 바꾸고, 가짜 창 클래스를 반환한다."""
    fake_app = MagicMock(wraps=app)
    fake_app.exec.return_value = 7
    monkeypatch.setattr(main_module, "QApplication", lambda _: fake_app)
    monkeypatch.setattr(main_module, "SingleInstance", lambda: instance)
    monkeypatch.setattr(main_module, "install_main_thread_gc", MagicMock())
    window_class = MagicMock()
    monkeypatch.setattr(main_module, "MainWindow", window_class)
    return window_class


def test_main_opens_window_when_first(
    app: QApplication,
    make_instance: Callable[[], SingleInstance],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """처음 켜면 창을 띄우고, 끝나면 잠금을 푸는지 확인한다."""
    window_class = _patch_main(monkeypatch, app, make_instance())

    assert main_module.main() == 7

    window_class.return_value.show.assert_called_once()
    assert make_instance().acquire()


def test_main_exits_without_window_when_already_running(
    app: QApplication,
    make_instance: Callable[[], SingleInstance],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이미 켜져 있으면 창을 만들지 않고 알린 뒤 끝나는지 확인한다."""
    first = make_instance()
    assert first.acquire()
    received = MagicMock()
    first.activation_requested.connect(received)
    window_class = _patch_main(monkeypatch, app, make_instance())

    assert main_module.main() == 0

    window_class.assert_not_called()
    assert _wait_until(lambda: received.call_count == 1)
