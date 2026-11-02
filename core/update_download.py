"""업데이트에 필요한 내용을 인터넷에서 받는다. 운영체제의 인증서를 쓰도록 Qt로 받는다."""

from collections.abc import Callable

from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from core.updater import UpdateError

TRANSFER_TIMEOUT_MS = 30000
STOP_CHECK_INTERVAL_MS = 100


def fetch_url(url: str, should_stop: Callable[[], bool] = lambda: False) -> bytes:
    """주소의 내용을 받아 반환한다. 실패하거나 중단되면 UpdateError를 발생시킨다."""
    manager = QNetworkAccessManager()
    request = QNetworkRequest(QUrl(url))
    # GitHub API는 User-Agent가 없는 요청을 거부한다.
    request.setRawHeader(b"User-Agent", b"zipup_helper")
    request.setTransferTimeout(TRANSFER_TIMEOUT_MS)
    reply = manager.get(request)

    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    stop_timer = QTimer()
    stop_timer.setInterval(STOP_CHECK_INTERVAL_MS)
    stop_timer.timeout.connect(lambda: should_stop() and reply.abort())
    stop_timer.start()
    loop.exec()
    stop_timer.stop()

    if reply.error() != QNetworkReply.NetworkError.NoError:
        raise UpdateError(reply.errorString())
    return bytes(reply.readAll())
