"""사진 썸네일과 미리보기를 화면이 멈추지 않도록 별도 스레드에서 만든다."""

from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader, QPainter

# 레티나 화면에서도 선명하도록 표시 크기(96)의 두 배로 만든다.
THUMBNAIL_SIZE = QSize(192, 192)
PREVIEW_SIZE = QSize(1600, 1600)


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


def _read_scaled(path: Path, size: QSize) -> tuple[QImage, QSize]:
    """사진을 size 안에 맞게 줄여 읽고, 회전을 반영한 원본 크기와 함께 반환한다."""
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
    if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
        original_size.transpose()
    return image, original_size


def load_thumbnail(path: Path, size: QSize = THUMBNAIL_SIZE) -> QImage:
    """사진을 size 안에 맞게 줄여 칸 가운데에 놓는다. 읽지 못하면 비어 있는 QImage를 반환한다."""
    image, _original_size = _read_scaled(path, size)
    return image if image.isNull() else _center_on_canvas(image, size)


def load_preview(path: Path, size: QSize = PREVIEW_SIZE) -> tuple[QImage, QSize]:
    """미리보기용 사진과 원본 크기를 반환한다. 읽지 못하면 비어 있는 QImage를 반환한다."""
    return _read_scaled(path, size)


class PreviewLoader(QThread):
    """사진 한 장의 미리보기를 읽어 신호로 보내는 스레드."""

    # 파일 경로, 미리보기, 원본 크기를 보낸다. 읽지 못하면 미리보기가 비어 있다.
    preview_ready = Signal(str, QImage, QSize)

    def __init__(self, path: Path, parent: QObject | None = None) -> None:
        """읽을 파일을 기억한다."""
        super().__init__(parent)
        self._path = path

    def run(self) -> None:
        """중단 요청이 없으면 미리보기를 읽어 보낸다."""
        if self.isInterruptionRequested():
            return
        image, original_size = load_preview(self._path)
        self.preview_ready.emit(str(self._path), image, original_size)


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
