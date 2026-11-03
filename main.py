"""zipup_helper 프로그램의 시작점."""

import sys
from functools import partial

from PySide6.QtWidgets import QApplication

from ui.gc_guard import install_main_thread_gc
from ui.main_window import MainWindow
from ui.single_instance import SingleInstance, bring_to_front


def main() -> int:
    """앱과 메인 창을 띄우고, 종료 코드를 반환한다. 이미 켜져 있으면 그 창을 앞으로 가져온다."""
    app = QApplication(sys.argv)
    instance = SingleInstance()
    if not instance.acquire():
        instance.notify_running()
        return 0
    install_main_thread_gc(app)
    window = MainWindow()
    instance.activation_requested.connect(partial(bring_to_front, window))
    window.show()
    window.check_for_update()
    try:
        return app.exec()
    finally:
        instance.release()


if __name__ == "__main__":
    sys.exit(main())
