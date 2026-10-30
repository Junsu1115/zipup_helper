"""PPTX 처리를 화면이 멈추지 않도록 별도 스레드에서 실행한다."""

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from core.pptx_import import import_pptx_files


class PptxImportWorker(QThread):
    """PPTX를 처리하고 결과(PptxImportSummary)를 신호로 보내는 스레드."""

    import_finished = Signal(object)

    def __init__(
        self, sources: list[Path], project: Path, parent: QObject | None = None
    ) -> None:
        """처리할 파일과 저장할 프로젝트를 기억한다."""
        super().__init__(parent)
        self._sources = sources
        self._project = project

    def run(self) -> None:
        """파일을 처리하고 결과를 보낸다."""
        self.import_finished.emit(import_pptx_files(self._sources, self._project))
