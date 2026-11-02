"""동영상의 앞부분 한 장면을 화면을 막지 않고 썸네일로 가져온다."""

from collections import deque
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink

from ui.thumbnail_loader import fit_thumbnail

# 이 시간 안에 장면을 받지 못하면 그 동영상은 건너뛴다.
GRAB_TIMEOUT_MS = 5000


class _SingleVideoGrab(QObject):
    """동영상 하나의 첫 장면을 가져온다. 자기 경로를 가져, 장면이 늦게 와도 섞이지 않는다."""

    frame_ready = Signal(str, QImage)
    failed = Signal()

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        """재생기와 장면 수신기를 준비한다."""
        super().__init__(parent)
        self._path = path
        self._is_finished = False

        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame_changed)
        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.errorOccurred.connect(self._on_player_error)

    def start(self) -> None:
        """동영상을 소리 없이 재생한다."""
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))
        self._player.play()

    def stop(self) -> None:
        """재생을 멈추고 정리한다. 이후에 도착하는 장면은 무시한다."""
        self._is_finished = True
        self._player.stop()
        self.deleteLater()

    def _on_frame_changed(self, frame: QVideoFrame) -> None:
        """첫 번째 유효한 장면을 썸네일로 만들어 보낸다."""
        if self._is_finished or not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self._is_finished = True
            self.frame_ready.emit(str(self._path), fit_thumbnail(image))

    def _on_player_error(self, error: QMediaPlayer.Error, _message: str) -> None:
        """재생할 수 없으면 실패를 알린다."""
        if error != QMediaPlayer.Error.NoError and not self._is_finished:
            self._is_finished = True
            self.failed.emit()


class VideoThumbnailGrabber(QObject):
    """동영상을 차례로 열어 첫 장면을 신호로 보낸다. 가져오지 못한 동영상은 건너뛴다."""

    # 파일 경로와 썸네일을 보낸다.
    thumbnail_ready = Signal(str, QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        """대기 목록과 제한 시간 타이머를 준비한다."""
        super().__init__(parent)
        self._queue: deque[Path] = deque()
        self._active: _SingleVideoGrab | None = None

        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(GRAB_TIMEOUT_MS)
        self._timeout.timeout.connect(self._start_next)

    def grab(self, paths: Iterable[Path]) -> None:
        """동영상들의 썸네일 가져오기를 시작한다."""
        self._queue.extend(paths)
        if self._active is None:
            self._start_next()

    def stop(self) -> None:
        """남은 작업을 버리고 재생을 멈춘다."""
        self._queue.clear()
        self._stop_active()

    def _stop_active(self) -> None:
        """진행 중인 동영상의 작업을 정리한다."""
        self._timeout.stop()
        if self._active is not None:
            self._active.stop()
            self._active = None

    def _start_next(self) -> None:
        """다음 동영상의 장면 가져오기를 시작한다."""
        self._stop_active()
        if not self._queue:
            return
        self._active = _SingleVideoGrab(self._queue.popleft(), self)
        self._active.frame_ready.connect(self._on_frame_ready)
        self._active.failed.connect(self._start_next)
        self._timeout.start()
        self._active.start()

    def _on_frame_ready(self, path: str, image: QImage) -> None:
        """받은 썸네일을 보내고 다음 동영상으로 넘어간다."""
        self.thumbnail_ready.emit(path, image)
        self._start_next()
