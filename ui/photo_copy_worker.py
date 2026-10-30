"""사진 복사를 화면이 멈추지 않도록 별도 스레드에서 실행한다."""

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from core.photo_copy import copy_photos_to_pub


class PhotoCopyWorker(QThread):
    """사진을 복사하고 결과(PhotoCopySummary)를 신호로 보내는 스레드."""

    copy_finished = Signal(object)

    def __init__(
        self, sources: list[Path], project: Path, parent: QObject | None = None
    ) -> None:
        """복사할 사진과 프로젝트를 기억한다."""
        super().__init__(parent)
        self._sources = sources
        self._project = project

    def run(self) -> None:
        """사진을 복사하고 결과를 보낸다."""
        self.copy_finished.emit(copy_photos_to_pub(self._sources, self._project))
