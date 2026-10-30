"""zipup_helper 프로그램의 시작점."""

import sys

from PySide6.QtWidgets import QApplication

from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow


def main() -> int:
    """앱과 메인 창을 띄우고, 종료 코드를 반환한다."""
    app = QApplication(sys.argv)
    install_main_thread_gc(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
