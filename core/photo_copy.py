"""Work 폴더의 파일을 같은 위치의 Pub 폴더로 복사하고, Pub 폴더의 파일을 삭제한다."""

import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile

WORK_FOLDER = "Work"
PUB_FOLDER = "Pub"


@dataclass
class PhotoCopySummary:
    """여러 파일을 복사한 결과."""

    copied_count: int = 0
    existing_count: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class PhotoDeleteSummary:
    """여러 파일을 삭제한 결과."""

    deleted_count: int = 0
    failures: list[str] = field(default_factory=list)


def _is_inside(folder: Path, parent: Path) -> bool:
    """folder가 parent이거나 그 안에 있는지 반환한다."""
    return folder == parent or parent in folder.parents


def is_inside_pub(folder: Path, project: Path) -> bool:
    """folder가 프로젝트의 Pub 폴더이거나 그 안에 있는지 반환한다."""
    return _is_inside(folder, project / PUB_FOLDER)


def pub_destination(folder: Path, project: Path) -> Path | None:
    """Work 안의 폴더에 대응하는 Pub 폴더를 반환한다. Work 밖이면 None을 반환한다.

    예를 들어 Work/Image는 Pub/Image, Work/Video는 Pub/Video에 대응한다.
    """
    work_folder = project / WORK_FOLDER
    if not _is_inside(folder, work_folder):
        return None
    return project / PUB_FOLDER / folder.relative_to(work_folder)


def _copy_one(source: Path, project: Path, summary: PhotoCopySummary) -> None:
    """파일 하나를 대응하는 Pub 폴더로 복사하고 결과를 summary에 기록한다."""
    destination = pub_destination(source.parent, project)
    if destination is None:
        summary.failures.append(f"'{source.name}': Work 폴더의 파일만 복사할 수 있습니다.")
        return

    target = destination / source.name
    if target.exists():
        summary.existing_count += 1
        return
    try:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as error:
        # 복사하다 멈춘 파일이 남으면 다음에 "이미 있음"으로 처리되므로 지운다.
        if target.is_file():
            target.unlink(missing_ok=True)
        summary.failures.append(f"'{source.name}': {error}")
    else:
        summary.copied_count += 1


def copy_photos_to_pub(sources: Iterable[Path], project: Path) -> PhotoCopySummary:
    """파일을 대응하는 Pub 폴더에 복사한다. 폴더가 없으면 만들고, 같은 이름은 덮어쓰지 않는다.

    한 개가 실패해도 나머지는 계속 복사하고, 이미 복사한 파일은 그대로 둔다.
    """
    summary = PhotoCopySummary()
    for source in sources:
        _copy_one(source, project, summary)
    return summary


def _move_to_trash(path: Path) -> bool:
    """파일을 휴지통으로 옮기고 성공 여부를 반환한다."""
    return QFile.moveToTrash(str(path))


def delete_from_pub(paths: Iterable[Path], project: Path) -> PhotoDeleteSummary:
    """Pub 폴더의 파일을 휴지통으로 옮긴다. 실수로 지우지 않도록 Pub 밖의 파일은 거부한다."""
    summary = PhotoDeleteSummary()
    for path in paths:
        if not is_inside_pub(path.parent, project) or not path.is_file():
            summary.failures.append(
                f"'{path.name}': Pub 폴더의 파일이 아니어서 삭제하지 않았습니다."
            )
        elif _move_to_trash(path):
            summary.deleted_count += 1
        else:
            summary.failures.append(f"'{path.name}': 휴지통으로 옮기지 못했습니다.")
    return summary
