"""폴더 변화를 감지해 짧은 시간의 변경을 한 번으로 묶어 알리는 감시기."""

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

# 폴더 변경 알림이 연달아 와도 한 번만 알리도록 기다리는 시간.
RELOAD_DELAY_MS = 200


class DebouncedFolderWatcher(QObject):
    """감시 중인 폴더가 바뀌면, 마지막 변경 후 잠시 기다렸다가 changed를 보낸다."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """감시기와 대기 타이머를 설정한다."""
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RELOAD_DELAY_MS)
        self._timer.timeout.connect(self.changed)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)

    def set_paths(self, paths: Iterable[Path]) -> None:
        """감시할 폴더를 paths로 교체한다."""
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        new_paths = [str(path) for path in paths]
        if new_paths:
            self._watcher.addPaths(new_paths)

    def _on_directory_changed(self, _path: str) -> None:
        """폴더가 바뀌면 대기 타이머를 처음부터 다시 시작한다."""
        self._timer.start()
