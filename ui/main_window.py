"""프로그램의 메인 창."""

from functools import partial
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QMainWindow, QSplitter, QVBoxLayout, QWidget

from core.pptx_import import DroppedFiles, PptxImportSummary, classify_dropped_files
from ui.folder_browser import FolderBrowser
from ui.message_boxes import confirm, warn
from ui.photo_panel import PhotoPanel
from ui.pptx_import_worker import PptxImportWorker
from ui.project_selector import ProjectSelector
from ui.toast import show_toast

IMPORT_TITLE = "PPT 저장"
PANEL_WIDTH = 260


def _local_paths(event: QDragEnterEvent | QDropEvent) -> list[Path]:
    """끌어다 놓은 항목 중 이 컴퓨터에 있는 파일의 경로를 반환한다."""
    return [
        Path(url.toLocalFile())
        for url in event.mimeData().urls()
        if url.isLocalFile()
    ]


def _skipped_file_messages(dropped: DroppedFiles) -> list[str]:
    """처리하지 않는 파일에 대한 안내 문구를 만든다."""
    messages = [
        f"'{path.name}': 구형 PPT 파일입니다. PPTX로 저장한 뒤 넣어 주세요."
        for path in dropped.legacy_ppt
    ]
    messages += [
        f"'{path.name}': PPTX 파일이 아니어서 건너뛰었습니다."
        for path in dropped.unsupported
    ]
    return messages


class MainWindow(QMainWindow):
    """기능 화면이 추가될 메인 창. PPTX 파일을 끌어다 놓을 수 있다."""

    def __init__(self) -> None:
        """창 제목과 크기를 설정하고, 프로젝트 선택 아래에 폴더 화면과 사진 칸을 배치한다."""
        super().__init__()
        self.setWindowTitle("zipup_helper")
        self.resize(960, 640)
        self.setAcceptDrops(True)
        self._import_worker: PptxImportWorker | None = None
        self._skipped_messages: list[str] = []

        self._selector = ProjectSelector()
        browser = FolderBrowser()
        browser.set_project(self._selector.current_project())
        self._selector.project_changed.connect(browser.set_project)

        photo_panel = PhotoPanel()
        browser.image_selected.connect(photo_panel.set_image)
        browser.image_activated.connect(photo_panel.open_with_default_app)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(browser)
        splitter.addWidget(photo_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([self.width() - PANEL_WIDTH, PANEL_WIDTH])

        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        layout.addWidget(self._selector)
        layout.addWidget(splitter, stretch=1)
        self.setCentralWidget(central_widget)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """밖에서 끌어온 이 컴퓨터의 파일만 놓을 수 있게 한다. 프로그램 안의 사진은 무시한다."""
        if event.source() is None and _local_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        """놓은 파일을 받는다. 확인창이 Finder를 붙잡지 않도록 처리는 드롭이 끝난 뒤로 미룬다."""
        event.acceptProposedAction()
        QTimer.singleShot(0, partial(self._handle_dropped_paths, _local_paths(event)))

    def closeEvent(self, event: QCloseEvent) -> None:
        """진행 중인 PPTX 처리가 끝나기를 기다린 뒤 창을 닫는다."""
        if self._import_worker is not None:
            self._import_worker.wait()
        super().closeEvent(event)

    def _handle_dropped_paths(self, paths: list[Path]) -> None:
        """현재 프로젝트에 저장할지 묻고, 예를 누르면 PPTX 처리를 시작한다."""
        if self._import_worker is not None:
            warn(self, IMPORT_TITLE, "PPT를 저장하는 중입니다. 끝난 뒤 다시 넣어 주세요.")
            return

        project = self._selector.current_project()
        if project is None:
            warn(self, IMPORT_TITLE, "PPT를 저장할 프로젝트가 없습니다.")
            return

        dropped = classify_dropped_files(paths)
        skipped_messages = _skipped_file_messages(dropped)
        if not dropped.pptx:
            warn(self, IMPORT_TITLE, "\n".join(skipped_messages))
            return

        count = len(dropped.pptx)
        target = "PPT를" if count == 1 else f"PPT {count}개를"
        if not confirm(
            self, IMPORT_TITLE, f"'{project.name}' 프로젝트에 {target} 저장하시겠습니까?"
        ):
            return

        self._skipped_messages = skipped_messages
        self._start_import(dropped.pptx, project)

    def _start_import(self, sources: list[Path], project: Path) -> None:
        """별도 스레드에서 PPTX 처리를 시작한다."""
        worker = PptxImportWorker(sources, project, self)
        worker.import_finished.connect(self._on_import_finished)
        worker.finished.connect(self._on_worker_stopped)
        self._import_worker = worker
        worker.start()

    def _on_worker_stopped(self) -> None:
        """끝난 스레드를 정리한다."""
        self._import_worker.deleteLater()
        self._import_worker = None

    def _on_import_finished(self, summary: PptxImportSummary) -> None:
        """처리 결과를 알림과 경고창으로 보여준다."""
        if summary.media_count:
            show_toast(self, f"사진 {summary.media_count}개를 저장했습니다")
        elif summary.imported_count:
            show_toast(self, "PPT를 저장했습니다. 꺼낼 사진은 없었습니다")

        messages = [*summary.failures, *self._skipped_messages]
        if messages:
            warn(self, IMPORT_TITLE, "\n".join(messages))
