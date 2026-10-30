"""Python의 메모리 자동 정리(GC)를 메인 스레드에서만 실행되게 한다.

자동 정리가 작업 스레드에서 실행되면 이미 지워진 Qt 객체를 다시 지우려다
프로그램이 비정상 종료될 수 있다.
"""

import gc

from PySide6.QtCore import QObject, QTimer

GC_INTERVAL_MS = 1000
GC_TIMER_NAME = "main_thread_gc_timer"


def install_main_thread_gc(parent: QObject) -> QTimer:
    """자동 정리를 끄고, 메인 스레드의 타이머로 주기적으로 실행하게 한다.

    메인 스레드에서 호출해야 한다. 이미 설치되어 있으면 기존 타이머를 반환한다.
    """
    existing_timer = parent.findChild(QTimer, GC_TIMER_NAME)
    if existing_timer is not None:
        return existing_timer

    gc.disable()
    timer = QTimer(parent)
    timer.setObjectName(GC_TIMER_NAME)
    timer.setInterval(GC_INTERVAL_MS)
    timer.timeout.connect(gc.collect)
    timer.start()
    return timer
