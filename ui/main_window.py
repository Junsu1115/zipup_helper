"""프로그램의 메인 창."""

from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ui.folder_browser import FolderBrowser
from ui.project_selector import ProjectSelector


class MainWindow(QMainWindow):
    """기능 화면이 추가될 메인 창."""

    def __init__(self) -> None:
        """창 제목과 크기를 설정하고, 프로젝트 선택 위젯 아래에 폴더 화면을 배치한다."""
        super().__init__()
        self.setWindowTitle("zipup_helper")
        self.resize(960, 640)

        selector = ProjectSelector()
        browser = FolderBrowser()
        browser.set_project(selector.current_project())
        selector.project_changed.connect(browser.set_project)

        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        layout.addWidget(selector)
        layout.addWidget(browser, stretch=1)
        self.setCentralWidget(central_widget)
