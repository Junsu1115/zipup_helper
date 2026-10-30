"""프로그램의 메인 창."""

from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ui.project_selector import ProjectSelector


class MainWindow(QMainWindow):
    """기능 화면이 추가될 메인 창."""

    def __init__(self) -> None:
        """창 제목과 크기를 설정하고, 상단에 프로젝트 폴더 선택 위젯을 배치한다."""
        super().__init__()
        self.setWindowTitle("zipup_helper")
        self.resize(960, 640)

        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        layout.addWidget(ProjectSelector())
        layout.addStretch()
        self.setCentralWidget(central_widget)
