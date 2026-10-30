"""사진 썸네일을 화면이 멈추지 않도록 별도 스레드에서 만든다."""

from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter

# 레티나 화면에서도 선명하도록 표시 크기(96)의 두 배로 만든다.
THUMBNAIL_SIZE = QSize(192, 192)


def _center_on_canvas(image: QImage, size: QSize) -> QImage:
    """사진을 투명한 size 칸 가운데에 놓는다. 비율이 달라도 아이콘 크기가 같아진다."""
    canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage(
        (size.width() - image.width()) // 2,
        (size.height() - image.height()) // 2,
        image,
    )
    painter.end()
    return canvas


def load_thumbnail(path: Path, size: QSize = THUMBNAIL_SIZE) -> QImage:
    """사진을 size 안에 맞게 줄여 칸 가운데에 놓는다. 읽지 못하면 비어 있는 QImage를 반환한다."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original_size = reader.size()
    if original_size.isValid() and (
        original_size.width() > size.width()
        or original_size.height() > size.height()
    ):
        reader.setScaledSize(
            original_size.scaled(size, Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    return image if image.isNull() else _center_on_canvas(image, size)


class ThumbnailLoader(QThread):
    """폴더의 사진 썸네일을 차례로 만들어, 하나씩 신호로 보내는 스레드."""

    # 폴더 경로, 파일 이름, 썸네일을 보낸다.
    thumbnail_ready = Signal(str, str, QImage)

    def __init__(
        self, folder: Path, names: list[str], parent: QObject | None = None
    ) -> None:
        """처리할 폴더와 파일 이름을 기억한다."""
        super().__init__(parent)
        self._folder = folder
        self._names = names

    def run(self) -> None:
        """썸네일을 만든다. 중단 요청을 받으면 다음 사진으로 넘어가지 않고 끝낸다."""
        for name in self._names:
            if self.isInterruptionRequested():
                return
            image = load_thumbnail(self._folder / name)
            if not image.isNull():
                self.thumbnail_ready.emit(str(self._folder), name, image)
