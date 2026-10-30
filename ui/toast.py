"""메인 창 하단에 잠깐 표시되었다가 저절로 사라지는 알림."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

TOAST_DURATION_MS = 2000
BOTTOM_MARGIN = 16
TOAST_STYLE = """
    background-color: rgba(40, 40, 40, 220);
    color: white;
    padding: 6px 14px;
    border-radius: 6px;
"""


class _Toast(QLabel):
    """메인 창 위에 겹쳐 표시되는 작은 알림. 마우스 조작을 가로막지 않는다."""

    def __init__(self, window: QWidget) -> None:
        """모양과 사라지는 타이머를 설정한다."""
        super().__init__(window)
        self.setStyleSheet(TOAST_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def display(self, text: str) -> None:
        """창 하단 가운데에 문구를 표시하고, 사라질 때까지의 시간을 새로 센다."""
        self.setText(text)
        self.adjustSize()

        window = self.parentWidget()
        self.move(
            (window.width() - self.width()) // 2,
            window.height() - self.height() - BOTTOM_MARGIN,
        )
        self.raise_()
        self.show()
        self._hide_timer.start(TOAST_DURATION_MS)


def show_toast(parent: QWidget, text: str) -> None:
    """parent가 속한 최상위 창의 하단에 알림을 표시한다. 알림은 창마다 하나만 쓴다."""
    window = parent.window()
    toast = window.findChild(_Toast) or _Toast(window)
    toast.display(text)
