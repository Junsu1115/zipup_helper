"""파일을 프로그램 밖으로 끌어낸다. Finder에서 파일을 끌 때와 같은 형식으로 전달한다."""

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, QUrl
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import QFileIconProvider, QWidget

DRAG_PIXMAP_SIZE = QSize(96, 96)


def build_file_mime_data(path: Path) -> QMimeData:
    """파일 경로를 담은 드래그 데이터를 만든다."""
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(path))])
    return mime_data


def _drag_pixmap(path: Path, pixmap: QPixmap | None) -> QPixmap:
    """끄는 동안 표시할 이미지를 만든다. 사진이 없으면 일반 파일 아이콘을 쓴다."""
    if pixmap is None or pixmap.isNull():
        icon = QFileIconProvider().icon(QFileIconProvider.IconType.File)
        return icon.pixmap(DRAG_PIXMAP_SIZE)
    return pixmap.scaled(
        DRAG_PIXMAP_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def start_file_drag(
    source: QWidget, path: Path, pixmap: QPixmap | None = None
) -> Qt.DropAction:
    """파일 드래그를 시작하고 끝날 때까지 기다린다. 원본이 사라지지 않게 복사로만 전달한다."""
    drag_pixmap = _drag_pixmap(path, pixmap)
    drag = QDrag(source)
    drag.setMimeData(build_file_mime_data(path))
    drag.setPixmap(drag_pixmap)
    drag.setHotSpot(QPoint(drag_pixmap.width() // 2, drag_pixmap.height() // 2))
    return drag.exec(Qt.DropAction.CopyAction)
