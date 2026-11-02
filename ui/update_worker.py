"""업데이트 확인과 내려받기를 화면이 멈추지 않도록 별도 스레드에서 실행한다."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from core.update_download import fetch_url
from core.updater import UpdateError, archive_url, find_new_version, stage_update


class UpdateCheckWorker(QThread):
    """새 버전을 확인하는 스레드. 있을 때만 신호를 보내고, 실패는 조용히 넘어간다."""

    version_found = Signal(str)

    def __init__(self, current: str, parent: QObject | None = None) -> None:
        """지금 버전을 기억한다."""
        super().__init__(parent)
        self._current = current

    def run(self) -> None:
        """최신 버전을 확인한다. 인터넷이 없어도 프로그램은 그대로 쓸 수 있어야 한다."""
        fetch = partial(fetch_url, should_stop=self.isInterruptionRequested)
        try:
            version = find_new_version(self._current, fetch)
        except UpdateError:
            return
        if version is not None:
            self.version_found.emit(version)


class UpdateDownloadWorker(QThread):
    """새 버전을 내려받아 교체 직전 상태로 준비하는 스레드."""

    update_staged = Signal(object)
    update_failed = Signal(str)

    def __init__(self, version: str, root: Path, parent: QObject | None = None) -> None:
        """받을 버전과 프로그램 폴더를 기억한다."""
        super().__init__(parent)
        self._version = version
        self._root = root

    def run(self) -> None:
        """내려받아 준비하고 결과를 보낸다."""
        try:
            data = fetch_url(
                archive_url(self._version), should_stop=self.isInterruptionRequested
            )
            staged = stage_update(data, self._version, self._root)
        except (UpdateError, OSError) as error:
            self.update_failed.emit(str(error))
            return
        self.update_staged.emit(staged)
