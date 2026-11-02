"""끌어온 파일을 폴더 위에 놓는 처리. 밖의 파일은 복사하고, 안의 파일은 옮긴다."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QPushButton, QWidget

from core.folder_contents import is_media_file


def dragged_paths(event: QDropEvent) -> list[Path]:
    """끌어온 항목 중 이 컴퓨터에 있는 파일의 경로를 반환한다."""
    return [
        Path(url.toLocalFile())
        for url in event.mimeData().urls()
        if url.isLocalFile()
    ]


def is_external_file_drag(event: QDragEnterEvent) -> bool:
    """프로그램 밖에서 끌어온 파일인지 반환한다."""
    return event.source() is None and bool(dragged_paths(event))


def is_internal_file_drag(event: QDragEnterEvent) -> bool:
    """프로그램 안에서 끈 파일인지 반환한다."""
    return event.source() is not None and bool(dragged_paths(event))


@dataclass(frozen=True)
class DropRules:
    """폴더 위에 놓을 때의 판단과 처리 함수."""

    is_folder: Callable[[str], bool]
    drop_target: Callable[[str], str | None]
    on_drop: Callable[[str, list[Path]], None]
    on_invalid_drop: Callable[[], None]
    # 폴더가 아닌 곳에 놓았거나 사진, 동영상이 없을 때의 처리. PPT 저장 기능으로 넘긴다.
    on_other_drop: Callable[[list[Path]], None]
    # 지금 보는 위치 전체가 복사 대상이면 그 폴더 이름을 반환한다.
    area_target: Callable[[], str | None]
    # 프로그램 안의 항목을 이름의 폴더로 옮길 수 있으면 그 폴더 경로를 반환한다.
    move_target: Callable[[str, list[Path]], Path | None]
    on_move: Callable[[Path, list[Path]], None]
    # 놓은 폴더 이름(없으면 None)으로 파일을 바로 넣을 폴더를 반환한다. 없으면 None을 반환한다.
    folder_copy_target: Callable[[str | None], Path | None]
    on_folder_copy: Callable[[Path, list[Path]], None]


class FolderDropHandler:
    """목록에 놓기를 처리한다. 복사할 수 없는 폴더에 놓으면 경고를 요청한다."""

    def __init__(
        self,
        name_at: Callable[[QDropEvent], str | None],
        set_highlight: Callable[[str, bool], None],
        rules: DropRules,
    ) -> None:
        """항목 찾기, 강조, 판단 규칙을 기억한다."""
        self._name_at = name_at
        self._set_highlight = set_highlight
        self._rules = rules
        self._highlighted: str | None = None

    def _change_highlight(self, name: str | None) -> None:
        """강조할 항목을 바꾼다."""
        if name == self._highlighted:
            return
        if self._highlighted is not None:
            self._set_highlight(self._highlighted, False)
        if name is not None:
            self._set_highlight(name, True)
        self._highlighted = name

    def _move_target(self, name: str, event: QDropEvent) -> Path | None:
        """프로그램 안의 항목을 옮길 폴더 경로를 반환한다."""
        return self._rules.move_target(name, dragged_paths(event))

    def _drop_internal(self, event: QDropEvent) -> None:
        """프로그램 안의 항목을 옮길 수 있는 폴더에 놓았으면 이동을 요청한다."""
        name = self._name_at(event)
        destination = None if name is None else self._move_target(name, event)
        if destination is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        self._rules.on_move(destination, dragged_paths(event))

    def _folder_for_media(self, event: QDropEvent) -> str | None:
        """사진이나 동영상을 폴더 위로 끌고 있으면 그 폴더 이름을 반환한다."""
        name = self._name_at(event)
        if name is None or not self._rules.is_folder(name):
            return None
        return name if any(map(is_media_file, dragged_paths(event))) else None

    def drag_enter(self, event: QDragEnterEvent) -> None:
        """프로그램 밖의 파일과, 옮길 수 있는 위치에서 끈 프로그램 안의 항목을 받는다."""
        if is_external_file_drag(event) or (
            is_internal_file_drag(event) and self._rules.area_target() is not None
        ):
            event.accept()
        else:
            event.ignore()

    def drag_move(self, event: QDragMoveEvent) -> None:
        """끌고 올라간 폴더를 강조한다. 프로그램 안의 항목은 옮길 수 있는 폴더에만 놓을 수 있다."""
        if not is_external_file_drag(event):
            name = self._name_at(event)
            can_move = name is not None and self._move_target(name, event) is not None
            self._change_highlight(name if can_move else None)
            if can_move:
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                event.ignore()
            return
        self._change_highlight(self._folder_for_media(event))
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def drag_leave(self) -> None:
        """영역을 벗어나면 강조를 지운다."""
        self._change_highlight(None)

    def drop(self, event: QDropEvent) -> None:
        """복사할 수 있는 폴더면 복사를, 다른 폴더면 경고를, 그 밖에는 PPT 처리를 요청한다."""
        self._change_highlight(None)
        if not is_external_file_drag(event):
            self._drop_internal(event)
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        paths = dragged_paths(event)
        has_media = any(map(is_media_file, paths))
        folder = self._folder_for_media(event)
        copy_folder = self._rules.folder_copy_target(folder) if has_media else None
        if copy_folder is not None:
            self._rules.on_folder_copy(copy_folder, paths)
            return
        area_target = self._rules.area_target()
        if area_target is not None and has_media:
            self._rules.on_drop(area_target, paths)
            return
        if folder is None:
            self._rules.on_other_drop(paths)
            return
        target = self._rules.drop_target(folder)
        if target is None:
            self._rules.on_invalid_drop()
        else:
            self._rules.on_drop(target, paths)


class MoveDropButton(QPushButton):
    """프로그램 안의 항목을 놓으면 정해진 폴더로 옮기는 버튼. 뒤로 버튼에 쓴다."""

    def __init__(
        self,
        text: str,
        move_target: Callable[[list[Path]], Path | None],
        on_move: Callable[[Path, list[Path]], None],
        parent: QWidget | None = None,
    ) -> None:
        """대상 폴더를 찾는 함수와 이동 처리 함수를 기억한다."""
        super().__init__(text, parent)
        self._move_target = move_target
        self._on_move = on_move
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """옮길 수 있으면 버튼을 눌린 모양으로 강조하고 받는다."""
        paths = dragged_paths(event)
        if is_internal_file_drag(event) and self._move_target(paths) is not None:
            self.setDown(True)
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """버튼 위에서 계속 놓을 수 있게 한다."""
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        """버튼을 벗어나면 강조를 지운다."""
        self.setDown(False)

    def dropEvent(self, event: QDropEvent) -> None:
        """놓은 항목의 이동을 요청한다."""
        self.setDown(False)
        paths = dragged_paths(event)
        destination = self._move_target(paths)
        if is_internal_file_drag(event) and destination is not None:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self._on_move(destination, paths)
        else:
            event.ignore()
