"""파일을 정해진 폴더로 복사하고, Pub 폴더의 파일을 삭제한다."""

import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile

from core.folder_contents import is_image_file
from core.project_folders import is_hidden

TRASH_ATTEMPTS = 3
TRASH_RETRY_SECONDS = 0.2

WORK_FOLDER = "Work"
PUB_FOLDER = "Pub"
REFERENCE_FOLDER = "Reference"
IMAGE_FOLDER = "Image"
VIDEO_FOLDER = "Video"
# 프로그램 밖의 파일을 끌어다 놓을 수 있는 프로젝트 맨 위의 폴더.
DROP_TARGET_FOLDERS = (WORK_FOLDER, REFERENCE_FOLDER)


@dataclass
class PhotoCopySummary:
    """여러 파일을 복사한 결과."""

    copied_count: int = 0
    existing_count: int = 0
    failures: list[str] = field(default_factory=list)
    # 폴더를 복사했을 때, 복사할 파일이 없어도 폴더는 만들어졌는지 나타낸다.
    folder_created: bool = False


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


def _copy_to_folder(
    source: Path, destination: Path, summary: PhotoCopySummary
) -> None:
    """파일 하나를 destination으로 복사하고 결과를 summary에 기록한다."""
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
        destination = pub_destination(source.parent, project)
        if destination is None:
            summary.failures.append(
                f"'{source.name}': Work 폴더의 파일만 복사할 수 있습니다."
            )
        else:
            _copy_to_folder(source, destination, summary)
    return summary


def _copy_tree(source: Path, destination: Path, summary: PhotoCopySummary) -> None:
    """폴더 내용을 destination과 합친다. 숨김 항목과 폴더 바로가기는 제외한다."""
    try:
        destination.mkdir(parents=True, exist_ok=True)
        entries = sorted(source.iterdir(), key=lambda entry: entry.name)
    except OSError as error:
        summary.failures.append(f"'{source.name}': {error}")
        return

    for entry in entries:
        if is_hidden(entry):
            continue
        if entry.is_symlink() and entry.is_dir():
            continue
        if entry.is_dir():
            _copy_tree(entry, destination / entry.name, summary)
        else:
            _copy_to_folder(entry, destination, summary)


def copy_folder_to_pub(folder: Path, project: Path) -> PhotoCopySummary:
    """Work 안의 폴더를 같은 위치의 Pub 폴더로 복사한다.

    Pub에 같은 폴더가 있으면 합치고, 같은 이름의 파일은 덮어쓰지 않는다.
    """
    summary = PhotoCopySummary()
    destination = pub_destination(folder, project)
    if destination is None:
        summary.failures.append(f"'{folder.name}': Work 폴더 안만 복사할 수 있습니다.")
        return summary
    _copy_tree(folder, destination, summary)
    summary.folder_created = destination.is_dir()
    return summary


def drop_target_containing(folder: Path, project: Path) -> str | None:
    """folder가 Work나 Reference 또는 그 안이면 그 폴더 이름을 반환한다."""
    for target_name in DROP_TARGET_FOLDERS:
        if _is_inside(folder, project / target_name):
            return target_name
    return None


def drop_destination(source: Path, project: Path, target_name: str) -> Path:
    """놓은 파일이 들어갈 폴더를 반환한다.

    사진은 대상 폴더의 Image로, 동영상은 대상 폴더의 Video로 간다.
    """
    subfolder = IMAGE_FOLDER if is_image_file(source) else VIDEO_FOLDER
    return project / target_name / subfolder


def copy_files_to_drop_target(
    sources: Iterable[Path], project: Path, target_name: str
) -> PhotoCopySummary:
    """놓은 파일을 대상 폴더로 복사한다. 폴더가 없으면 만들고, 같은 이름은 덮어쓰지 않는다."""
    summary = PhotoCopySummary()
    for source in sources:
        destination = drop_destination(source, project, target_name)
        _copy_to_folder(source, destination, summary)
    return summary


def _move_to_trash(path: Path) -> bool:
    """파일을 휴지통으로 옮기고 성공 여부를 반환한다."""
    return QFile.moveToTrash(str(path))


def _move_to_trash_with_retry(path: Path) -> bool:
    """휴지통으로 옮긴다. Windows에서 사진을 읽는 동안에는 실패하므로 잠시 뒤 다시 시도한다."""
    for attempt in range(TRASH_ATTEMPTS):
        if attempt:
            time.sleep(TRASH_RETRY_SECONDS)
        if _move_to_trash(path):
            return True
    return False


def delete_from_pub(paths: Iterable[Path], project: Path) -> PhotoDeleteSummary:
    """Pub 폴더의 파일을 휴지통으로 옮긴다. 실수로 지우지 않도록 Pub 밖의 파일은 거부한다."""
    summary = PhotoDeleteSummary()
    for path in paths:
        if not is_inside_pub(path.parent, project) or not path.is_file():
            summary.failures.append(
                f"'{path.name}': Pub 폴더의 파일이 아니어서 삭제하지 않았습니다."
            )
        elif _move_to_trash_with_retry(path):
            summary.deleted_count += 1
        else:
            summary.failures.append(f"'{path.name}': 휴지통으로 옮기지 못했습니다.")
    return summary
