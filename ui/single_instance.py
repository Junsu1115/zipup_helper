"""프로그램이 하나만 켜지도록 잠그고, 두 번째 실행을 먼저 켜진 창에 알린다."""

import hashlib
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QObject, Qt, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QWidget

CONNECT_TIMEOUT_MS = 1000
# 0이면 시간이 지났다고 잠금을 뺏지 않고, 잠근 프로세스가 사라졌을 때만 치운다.
STALE_LOCK_DISABLED = 0


def _default_name() -> str:
    """사용자 계정마다 다른 이름을 반환한다. Windows의 통신 이름은 계정끼리 겹치기 때문이다."""
    digest = hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()
    return f"zipup_helper_{digest[:16]}"


class SingleInstance(QObject):
    """잠금 파일로 실행 여부를 판단하고, 두 번째 실행의 알림을 받는다."""

    activation_requested = Signal()

    def __init__(self, folder: Path | None = None, name: str | None = None) -> None:
        """잠금 파일을 둘 폴더와 통신 이름을 정한다. 생략하면 임시 폴더와 계정별 이름을 쓴다."""
        super().__init__()
        self._name = name or _default_name()
        lock_folder = folder or Path(QDir.tempPath())
        self._lock = QLockFile(str(lock_folder / f"{self._name}.lock"))
        self._lock.setStaleLockTime(STALE_LOCK_DISABLED)
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """먼저 켜진 프로그램이 없으면 잠그고 True를 반환한다.

        임시 폴더 권한 문제처럼 판단할 수 없는 경우에는 프로그램이 안 켜지는 것보다
        낫기 때문에 True를 반환한다.
        """
        if self._lock.tryLock(0):
            self._listen()
            return True
        return self._lock.error() != QLockFile.LockError.LockFailedError

    def notify_running(self) -> bool:
        """먼저 켜진 프로그램에 창을 앞으로 가져오라고 알리고 성공 여부를 반환한다."""
        socket = QLocalSocket()
        socket.connectToServer(self._name)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        # 상대가 연결을 받아 닫을 때까지 기다려, 알림이 전달되기 전에 끊지 않게 한다.
        socket.waitForDisconnected(CONNECT_TIMEOUT_MS)
        socket.abort()
        return True

    def release(self) -> None:
        """통신을 닫고 잠금을 푼다."""
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _listen(self) -> None:
        """두 번째 실행의 알림을 기다린다. 실패해도 프로그램은 그대로 쓸 수 있다."""
        # 잠금을 잡았으므로 남아 있는 통신 파일은 강제 종료된 프로그램의 것이다.
        QLocalServer.removeServer(self._name)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(self._name)

    def _on_new_connection(self) -> None:
        """알림이 오면 연결을 정리하고 신호를 보낸다."""
        while self._server is not None and self._server.hasPendingConnections():
            self._server.nextPendingConnection().deleteLater()
            self.activation_requested.emit()


def bring_to_front(window: QWidget) -> None:
    """창을 맨 앞으로 가져온다. 최소화되어 있으면 원래 크기로 되돌린다."""
    window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
