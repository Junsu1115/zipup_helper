"""복사할 파일의 썸네일을 보여주고 복사 여부를 묻는 창."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileIconProvider,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.folder_contents import is_image_file
from ui.message_boxes import center_on_parent_window
from ui.thumbnail_loader import ThumbnailLoader
from ui.video_thumbnail import VideoThumbnailGrabber

MAX_THUMBNAILS = 12
THUMBNAIL_COLUMNS = 4
THUMBNAIL_DISPLAY_SIZE = QSize(80, 80)
NAME_WIDTH = 96
FOLDER_NOTE = "사진은 Image, 동영상은 Video 폴더에 들어갑니다."


class CopyConfirmDialog(QDialog):
    """파일 썸네일, 복사 질문, OK/Cancel 버튼으로 이루어진 확인창."""

    def __init__(
        self,
        target_name: str,
        paths: list[Path],
        parent: QWidget | None = None,
        sorts_by_kind: bool = True,
    ) -> None:
        """파일을 격자로 배치하고 썸네일 가져오기를 시작한다."""
        super().__init__(parent)
        self.setWindowTitle("파일 복사")
        self._thumbnail_labels: dict[Path, QLabel] = {}
        shown_paths = paths[:MAX_THUMBNAILS]

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_thumbnail_grid(shown_paths))
        if len(paths) > MAX_THUMBNAILS:
            layout.addWidget(QLabel(f"외 {len(paths) - MAX_THUMBNAILS}개"))

        layout.addWidget(QLabel(f"'{target_name}' 폴더로 복사하시겠습니까?"))
        if sorts_by_kind:
            layout.addWidget(QLabel(FOLDER_NOTE))

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._image_loaders: list[ThumbnailLoader] = []
        self._video_grabber = VideoThumbnailGrabber(self)
        self._video_grabber.thumbnail_ready.connect(self._on_video_thumbnail_ready)
        self._start_loading(shown_paths)

    def _build_thumbnail_grid(self, paths: list[Path]) -> QGridLayout:
        """일반 파일 아이콘으로 시작하는 썸네일과 이름의 격자를 만든다."""
        grid = QGridLayout()
        file_icon = QFileIconProvider().icon(QFileIconProvider.IconType.File)
        for index, path in enumerate(paths):
            row, column = divmod(index, THUMBNAIL_COLUMNS)
            thumbnail = QLabel()
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumbnail.setPixmap(file_icon.pixmap(THUMBNAIL_DISPLAY_SIZE))
            self._thumbnail_labels[path] = thumbnail

            name = QLabel(
                self.fontMetrics().elidedText(
                    path.name, Qt.TextElideMode.ElideMiddle, NAME_WIDTH
                )
            )
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setFixedWidth(NAME_WIDTH)

            grid.addWidget(thumbnail, row * 2, column)
            grid.addWidget(name, row * 2 + 1, column)
        return grid

    def _start_loading(self, paths: list[Path]) -> None:
        """사진과 동영상의 썸네일 가져오기를 각각 시작한다."""
        images = [path for path in paths if is_image_file(path)]
        videos = [path for path in paths if not is_image_file(path)]
        # 밖에서 끌어온 파일은 여러 폴더에 있을 수 있어 폴더별로 읽는다.
        for folder in {path.parent for path in images}:
            names = [path.name for path in images if path.parent == folder]
            loader = ThumbnailLoader(folder, names, self)
            loader.thumbnail_ready.connect(self._on_image_thumbnail_ready)
            self._image_loaders.append(loader)
            loader.start()
        if videos:
            self._video_grabber.grab(videos)

    def _on_image_thumbnail_ready(self, folder: str, name: str, image: QImage) -> None:
        """받은 사진 썸네일을 표시한다."""
        self._show_thumbnail(Path(folder) / name, image)

    def _on_video_thumbnail_ready(self, path: str, image: QImage) -> None:
        """받은 동영상 썸네일을 표시한다."""
        self._show_thumbnail(Path(path), image)

    def _show_thumbnail(self, path: Path, image: QImage) -> None:
        """썸네일을 표시 크기로 줄여 해당 칸에 넣는다."""
        label = self._thumbnail_labels.get(path)
        if label is not None:
            label.setPixmap(
                QPixmap.fromImage(image).scaled(
                    THUMBNAIL_DISPLAY_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def showEvent(self, event: QShowEvent) -> None:
        """크기가 정해진 뒤 창을 메인 창 가운데로 옮긴다."""
        super().showEvent(event)
        center_on_parent_window(self)

    def done(self, result: int) -> None:
        """창을 닫기 전에 썸네일 가져오기를 멈춘다."""
        self._video_grabber.stop()
        for loader in self._image_loaders:
            loader.requestInterruption()
        for loader in self._image_loaders:
            loader.wait()
        super().done(result)
