"""선택한 사진의 미리보기와 정보를 보여주고, 외부 프로그램으로 여는 오른쪽 칸."""

from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.external_apps import (
    EXTERNAL_APPS,
    build_default_open_command,
    build_open_command,
)
from core.folder_contents import format_size, read_entry_details
from ui.file_drag import start_file_drag
from ui.message_boxes import warn
from ui.thumbnail_loader import PreviewLoader

MINIMUM_PANEL_WIDTH = 220
NO_SELECTION_TEXT = "사진을 선택하세요"
LOADING_TEXT = "불러오는 중..."
NO_PREVIEW_TEXT = "미리보기를 표시할 수 없습니다"
OPEN_ERROR_TITLE = "프로그램 열기 오류"


class _DraggablePreviewLabel(QLabel):
    """사진을 프로그램 밖으로 끌어낼 수 있는 미리보기."""

    def __init__(self) -> None:
        """끌어낼 사진이 없는 상태로 시작한다."""
        super().__init__()
        self.file_path: Path | None = None
        self.drag_pixmap: QPixmap | None = None
        self._press_position: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """왼쪽 버튼을 누른 위치를 기억한다."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """버튼을 떼면 끌기 준비를 취소한다."""
        self._press_position = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """누른 채 일정 거리 이상 움직이면 파일 드래그를 시작한다."""
        if self._press_position is not None and self.file_path is not None:
            distance = event.position().toPoint() - self._press_position
            if distance.manhattanLength() >= QApplication.startDragDistance():
                self._press_position = None
                start_file_drag(self, [self.file_path], self.drag_pixmap)
                return
        super().mouseMoveEvent(event)


class PhotoPanel(QWidget):
    """사진 미리보기, 이름, 크기 정보, 외부 프로그램 버튼으로 이루어진 칸."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """위젯을 위에서부터 차례로 배치한다."""
        super().__init__(parent)
        self.setMinimumWidth(MINIMUM_PANEL_WIDTH)
        self._image_path: Path | None = None
        self._preview: QPixmap | None = None
        self._preview_loaders: set[PreviewLoader] = set()
        self._open_process: QProcess | None = None
        self._open_error_message = ""
        QApplication.instance().aboutToQuit.connect(self.stop_preview_loading)

        self._preview_label = _DraggablePreviewLabel()
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setWordWrap(True)
        # 큰 사진이 칸의 크기를 밀어내지 않게 한다.
        self._preview_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored
        )
        self._name_label = QLabel()
        self._name_label.setWordWrap(True)
        self._info_label = QLabel()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._preview_label, stretch=1)
        layout.addWidget(self._name_label)
        layout.addWidget(self._info_label)

        self._app_buttons = QButtonGroup(self)
        for index, app in enumerate(EXTERNAL_APPS):
            button = QPushButton(app.name)
            self._app_buttons.addButton(button, index)
            layout.addWidget(button)
        self._app_buttons.idClicked.connect(self._on_app_button_clicked)

        self.set_image(None)

    def set_images(self, paths: list[Path]) -> None:
        """선택한 사진들을 표시한다. 여러 장이면 장수만 표시하고 버튼을 끈다."""
        if len(paths) > 1:
            self.set_image(None)
            self._preview_label.setText(f"파일 {len(paths)}개 선택됨")
        else:
            self.set_image(paths[0] if paths else None)

    def set_image(self, path: Path | None) -> None:
        """표시할 사진을 바꾼다. None이면 선택 안내를 보여주고 버튼을 끈다."""
        self._image_path = path
        self._preview = None
        self._preview_label.clear()
        self._preview_label.file_path = path
        self._preview_label.drag_pixmap = None
        self._stop_current_loaders()
        for button in self._app_buttons.buttons():
            button.setEnabled(path is not None)

        if path is None:
            self._preview_label.setText(NO_SELECTION_TEXT)
            self._name_label.clear()
            self._info_label.clear()
            return

        self._preview_label.setText(LOADING_TEXT)
        self._name_label.setText(path.name)
        self._info_label.setText(format_size(read_entry_details(path, False).size))
        self._start_preview_loading(path)

    def _start_preview_loading(self, path: Path) -> None:
        """별도 스레드에서 미리보기 읽기를 시작한다."""
        loader = PreviewLoader(path, self)
        loader.preview_ready.connect(self._on_preview_ready)
        loader.finished.connect(self._remove_finished_loaders)
        self._preview_loaders.add(loader)
        loader.start()

    def _stop_current_loaders(self) -> None:
        """시작 전인 미리보기 읽기에 중단을 요청한다. 화면을 막지 않도록 기다리지 않는다."""
        for loader in self._preview_loaders:
            loader.requestInterruption()

    def stop_preview_loading(self) -> None:
        """미리보기 읽기를 중단하고 스레드가 끝나기를 기다린다. 프로그램 종료 시 호출한다."""
        self._stop_current_loaders()
        for loader in self._preview_loaders:
            loader.wait()

    def _remove_finished_loaders(self) -> None:
        """끝난 미리보기 스레드를 정리한다."""
        for loader in [item for item in self._preview_loaders if item.isFinished()]:
            self._preview_loaders.discard(loader)
            loader.deleteLater()

    def _on_preview_ready(self, path: str, image: QImage, original_size: QSize) -> None:
        """받은 미리보기를 표시한다. 이전에 선택한 사진의 결과는 버린다."""
        if self._image_path is None or str(self._image_path) != path:
            return
        if image.isNull():
            self._preview_label.setText(NO_PREVIEW_TEXT)
            return

        self._preview = QPixmap.fromImage(image)
        self._preview_label.drag_pixmap = self._preview
        self._show_scaled_preview()
        file_size = format_size(read_entry_details(self._image_path, False).size)
        self._info_label.setText(
            f"{original_size.width()} × {original_size.height()}  ·  {file_size}"
        )

    def _show_scaled_preview(self) -> None:
        """미리보기를 현재 칸 크기에 맞게 비율을 유지해 표시한다."""
        if self._preview is None:
            return
        ratio = self.devicePixelRatioF()
        scaled = self._preview.scaled(
            self._preview_label.size() * ratio,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(ratio)
        self._preview_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """칸 크기가 바뀌면 미리보기를 다시 맞춘다."""
        super().resizeEvent(event)
        self._show_scaled_preview()

    def _on_app_button_clicked(self, app_index: int) -> None:
        """누른 버튼의 프로그램으로 선택한 사진을 연다."""
        if self._image_path is None or self._open_process is not None:
            return
        app = EXTERNAL_APPS[app_index]
        self._run_open_command(
            build_open_command(app, self._image_path),
            f"{app.name}에서 열지 못했습니다.\n"
            "프로그램이 설치되어 있는지 확인해 주세요.",
        )

    def open_with_default_app(self, path: Path) -> None:
        """사진을 macOS 기본 프로그램으로 연다. 다른 열기가 진행 중이면 무시한다."""
        if self._open_process is not None:
            return
        self._run_open_command(
            build_default_open_command(path),
            f"'{path.name}' 파일을 열지 못했습니다.\n"
            "이 파일을 열 수 있는 프로그램이 있는지 확인해 주세요.",
        )

    def _run_open_command(self, command: list[str], error_message: str) -> None:
        """화면을 막지 않도록 QProcess로 명령을 실행한다."""
        program, *arguments = command
        process = QProcess(self)
        process.finished.connect(self._on_open_finished)
        process.errorOccurred.connect(self._on_open_error)
        self._open_process = process
        self._open_error_message = error_message
        process.start(program, arguments)

    def _on_open_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        """실행 결과를 확인하고, 실패했으면 경고창을 띄운다."""
        if exit_code != 0:
            self._warn_open_failed()
        self._clear_open_process()

    def _on_open_error(self, error: QProcess.ProcessError) -> None:
        """명령 자체를 실행하지 못하면 경고창을 띄운다."""
        if error == QProcess.ProcessError.FailedToStart:
            self._warn_open_failed()
            self._clear_open_process()

    def _warn_open_failed(self) -> None:
        """프로그램을 열지 못했다는 경고창을 띄운다."""
        warn(self, OPEN_ERROR_TITLE, self._open_error_message)

    def _clear_open_process(self) -> None:
        """끝난 실행 프로세스를 정리한다."""
        if self._open_process is not None:
            self._open_process.deleteLater()
        self._open_process = None
