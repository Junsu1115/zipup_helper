"""메인 창 가운데에 뜨는 확인창과 경고창. 모든 알림창은 이 모듈의 함수로 띄운다."""

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget


def center_on_parent_window(dialog: QDialog) -> None:
    """창을 부모가 속한 최상위 창의 가운데로 옮긴다."""
    parent = dialog.parentWidget()
    if parent is None:
        return
    geometry = dialog.frameGeometry()
    geometry.moveCenter(parent.window().frameGeometry().center())
    dialog.move(geometry.topLeft())


class _CenteredMessageBox(QMessageBox):
    """메인 창 가운데에 뜨는 알림창. 위치를 지정하기 위해 macOS 기본 알림창을 쓰지 않는다."""

    def __init__(self, parent: QWidget, title: str, text: str) -> None:
        """제목과 내용을 설정한다."""
        super().__init__(parent)
        self.setOption(QMessageBox.Option.DontUseNativeDialog)
        self.setWindowTitle(title)
        self.setText(text)

    def showEvent(self, event: QShowEvent) -> None:
        """크기가 정해진 뒤 창을 가운데로 옮긴다."""
        super().showEvent(event)
        center_on_parent_window(self)


def confirm(parent: QWidget, title: str, text: str) -> bool:
    """예/아니오를 묻고, 예를 눌렀는지 반환한다."""
    box = _CenteredMessageBox(parent, title, text)
    box.setIcon(QMessageBox.Icon.Question)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    box.exec()
    return box.clickedButton() == box.button(QMessageBox.StandardButton.Yes)


def warn(parent: QWidget, title: str, text: str) -> None:
    """경고창을 띄우고, OK를 눌러 닫을 때까지 기다린다."""
    box = _CenteredMessageBox(parent, title, text)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
