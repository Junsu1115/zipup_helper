"""새 버전이 있을 때만 나타나는 업데이트 버튼."""

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QProcess, QThread
from PySide6.QtWidgets import QPushButton, QWidget

from core.updater import (
    PROGRAM_ROOT,
    WORK_FOLDER_NAME,
    apply_command,
    installed_version,
    take_update_failure,
)
from ui.message_boxes import confirm, warn
from ui.update_worker import UpdateCheckWorker, UpdateDownloadWorker

DIALOG_TITLE = "업데이트"
BUTTON_TEXT = "업데이트"
DOWNLOADING_TEXT = "받는 중..."
CONFIRM_TEXT = (
    "새 버전으로 업데이트하시겠습니까?\n"
    "프로그램이 꺼졌다가 다시 켜지며, 몇 분 걸릴 수 있습니다.\n"
    "진행 중인 작업이 있으면 끝난 뒤 눌러 주세요."
)


class UpdateButton(QPushButton):
    """새 버전을 확인하고, 누르면 내려받아 프로그램을 교체하는 버튼."""

    def __init__(self, root: Path = PROGRAM_ROOT, parent: QWidget | None = None) -> None:
        """처음에는 숨겨 둔다."""
        super().__init__(BUTTON_TEXT, parent)
        self._root = root
        self._version: str | None = None
        self._worker: QThread | None = None
        self.clicked.connect(self._on_clicked)
        self.hide()

    def check_for_update(self) -> None:
        """지난 업데이트 실패를 알리고, 업데이트할 수 있는 설치본이면 새 버전을 확인한다."""
        if take_update_failure(self._root):
            warn(
                self,
                DIALOG_TITLE,
                "업데이트하지 못해 이전 버전으로 켰습니다. 다시 시도해 주세요.",
            )
        current = installed_version(self._root)
        if current is None:
            return
        worker = UpdateCheckWorker(current, self)
        worker.version_found.connect(self._on_version_found)
        self._start_worker(worker)

    def stop(self) -> None:
        """진행 중인 확인이나 내려받기를 멈추고 끝나기를 기다린다."""
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait()

    def _start_worker(self, worker: QThread) -> None:
        """스레드를 시작하고, 끝나면 정리하도록 연결한다."""
        worker.finished.connect(self._on_worker_stopped)
        self._worker = worker
        worker.start()

    def _on_worker_stopped(self) -> None:
        """끝난 스레드를 정리한다."""
        self._worker.deleteLater()
        self._worker = None

    def _on_version_found(self, version: str) -> None:
        """새 버전을 기억하고 버튼을 보여 준다."""
        self._version = version
        self.show()

    def _on_clicked(self) -> None:
        """확인을 받은 뒤 내려받기를 시작한다."""
        if self._worker is not None or not confirm(self, DIALOG_TITLE, CONFIRM_TEXT):
            return
        self.setEnabled(False)
        self.setText(DOWNLOADING_TEXT)
        worker = UpdateDownloadWorker(self._version, self._root, self)
        worker.update_staged.connect(self._on_update_staged)
        worker.update_failed.connect(self._on_update_failed)
        self._start_worker(worker)

    def _on_update_staged(self, staged: Path) -> None:
        """교체 프로그램을 띄우고 창을 닫는다. 교체는 이 프로그램이 꺼진 뒤 시작된다."""
        program, arguments = apply_command(
            staged, QCoreApplication.applicationPid(), self._root
        )
        started, _process_id = QProcess.startDetached(
            program, arguments, str(self._root / WORK_FOLDER_NAME)
        )
        if not started:
            self._on_update_failed("교체 프로그램을 실행하지 못했습니다.")
            return
        self.window().close()

    def _on_update_failed(self, message: str) -> None:
        """실패를 알리고 다시 누를 수 있게 한다."""
        self.setEnabled(True)
        self.setText(BUTTON_TEXT)
        warn(self, DIALOG_TITLE, f"업데이트하지 못했습니다.\n{message}")
