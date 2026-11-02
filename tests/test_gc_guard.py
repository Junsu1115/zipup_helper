"""ui.gc_guard 테스트."""

import gc
import os
import weakref
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.gc_guard import GC_INTERVAL_MS, install_main_thread_gc


@pytest.fixture(scope="session")
def app() -> Iterator[QApplication]:
    """화면에 창을 띄우지 않는 QApplication을 제공한다."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    application = QApplication.instance() or QApplication([])
    install_main_thread_gc(application)
    yield application


class _Node:
    """순환 참조를 만들기 위한 객체."""

    partner: "_Node"


def test_install_disables_automatic_gc_and_starts_timer(app: QApplication) -> None:
    """자동 정리를 끄고, 정해진 주기의 타이머를 시작하는지 확인한다."""
    timer = install_main_thread_gc(QObject(app))

    assert not gc.isenabled()
    assert timer.isActive()
    assert timer.interval() == GC_INTERVAL_MS
    timer.stop()


def test_install_twice_keeps_single_timer(app: QApplication) -> None:
    """두 번 호출해도 타이머가 하나만 있는지 확인한다."""
    parent = QObject(app)

    first = install_main_thread_gc(parent)
    second = install_main_thread_gc(parent)

    assert first is second
    first.stop()


def test_timer_collects_cyclic_garbage(app: QApplication) -> None:
    """자동 정리를 꺼도 타이머가 순환 참조를 정리하는지 확인한다."""
    first = _Node()
    second = _Node()
    first.partner = second
    second.partner = first
    reference = weakref.ref(first)
    del first, second

    assert reference() is not None

    QTest.qWait(GC_INTERVAL_MS + 300)

    assert reference() is None
