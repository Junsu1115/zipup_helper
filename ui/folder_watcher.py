"""폴더 변화를 감지해 짧은 시간의 변경을 한 번으로 묶어 알리는 감시기."""

import os
import sys
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

# 폴더 변경 알림이 연달아 와도 한 번만 알리도록 기다리는 시간.
RELOAD_DELAY_MS = 200
POLL_INTERVAL_MS = 1000

FolderSnapshot = frozenset[tuple[str, int, int]] | None


class _FolderChangeSource(QObject):
    """폴더 변화를 알아내는 방식(전략 패턴). 운영체제에 따라 구현을 고른다."""

    changed = Signal()

    def set_paths(self, paths: list[str]) -> None:
        """감시할 폴더를 paths로 교체한다."""
        raise NotImplementedError


class _SystemChangeSource(_FolderChangeSource):
    """운영체제의 알림으로 변화를 받는다. 알림이 바로 오고 폴더를 붙잡지 않는 Mac에서 쓴다."""

    def __init__(self, parent: QObject | None = None) -> None:
        """Qt 감시기를 설정한다."""
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self.changed)

    def set_paths(self, paths: list[str]) -> None:
        """감시할 폴더를 paths로 교체한다."""
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        if paths:
            self._watcher.addPaths(paths)


def _read_snapshot(path: str) -> FolderSnapshot:
    """폴더 안 항목의 이름, 수정 시각, 크기를 읽는다. 읽을 수 없으면 None을 반환한다."""
    try:
        with os.scandir(path) as entries:
            snapshot = []
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                snapshot.append((entry.name, info.st_mtime_ns, info.st_size))
    except OSError:
        return None
    return frozenset(snapshot)


class _PollingChangeSource(_FolderChangeSource):
    """폴더 내용을 주기적으로 비교한다.

    Windows의 알림 방식은 감시 중인 폴더를 계속 붙잡아, 그 폴더와 상위 폴더의
    이름을 바꾸거나 옮기지 못하게 하므로 Windows에서는 이 방식을 쓴다.
    """

    def __init__(
        self, parent: QObject | None = None, interval_ms: int = POLL_INTERVAL_MS
    ) -> None:
        """비교 타이머를 설정한다."""
        super().__init__(parent)
        self._snapshots: dict[str, FolderSnapshot] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._compare)

    def set_paths(self, paths: list[str]) -> None:
        """감시할 폴더를 paths로 교체하고, 지금 상태를 비교 기준으로 삼는다."""
        self._snapshots = {path: _read_snapshot(path) for path in paths}
        if paths:
            self._timer.start()
        else:
            self._timer.stop()

    def _compare(self) -> None:
        """기준과 다른 폴더가 있으면 기준을 새로 잡고 변화를 알린다."""
        current = {path: _read_snapshot(path) for path in self._snapshots}
        if current != self._snapshots:
            self._snapshots = current
            self.changed.emit()


def _create_change_source(
    parent: QObject, platform: str = sys.platform
) -> _FolderChangeSource:
    """운영체제에 맞는 감시 방식을 만든다."""
    if platform == "win32":
        return _PollingChangeSource(parent)
    return _SystemChangeSource(parent)


class DebouncedFolderWatcher(QObject):
    """감시 중인 폴더가 바뀌면, 마지막 변경 후 잠시 기다렸다가 changed를 보낸다."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        """감시 방식과 대기 타이머를 설정한다."""
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RELOAD_DELAY_MS)
        self._timer.timeout.connect(self.changed)

        self._source = _create_change_source(self)
        self._source.changed.connect(self._on_directory_changed)

    def set_paths(self, paths: Iterable[Path]) -> None:
        """감시할 폴더를 paths로 교체한다."""
        self._source.set_paths([str(path) for path in paths])

    def _on_directory_changed(self, _path: str = "") -> None:
        """폴더가 바뀌면 대기 타이머를 처음부터 다시 시작한다."""
        self._timer.start()
