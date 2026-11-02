"""파일 복사를 화면이 멈추지 않도록 별도 스레드에서 실행한다."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from core.photo_copy import PhotoCopySummary


class PhotoCopyWorker(QThread):
    """복사 작업을 실행하고 결과(PhotoCopySummary)를 신호로 보내는 스레드."""

    copy_finished = Signal(object)

    def __init__(
        self, copy_task: Callable[[], PhotoCopySummary], parent: QObject | None = None
    ) -> None:
        """실행할 복사 작업을 기억한다."""
        super().__init__(parent)
        self._copy_task = copy_task

    def run(self) -> None:
        """복사 작업을 실행하고 결과를 보낸다."""
        self.copy_finished.emit(self._copy_task())
