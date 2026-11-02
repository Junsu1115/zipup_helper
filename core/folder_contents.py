"""폴더 안의 사진과 동영상 파일 목록, 항목 정보 조회."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.project_folders import _sort_key, is_hidden

FOLDER_KIND = "폴더"
UNKNOWN_VALUE = "--"
SIZE_UNITS = ("KB", "MB", "GB", "TB")

IMAGE_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".emf",
        ".wmf",
        ".heic",
    }
)
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"})
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES


@dataclass(frozen=True)
class EntryDetails:
    """자세히 보기에 표시할 항목 정보. 알 수 없는 값은 None이다."""

    modified: datetime | None
    size: int | None
    kind: str


def name_sort_key(name: str) -> tuple[tuple[str | int, ...], str]:
    """이름 정렬 기준을 반환한다. 목록 조회와 같은 기준을 쓴다."""
    return _sort_key(name)


def is_image_file(path: Path) -> bool:
    """사진 형식의 파일 이름인지 반환한다."""
    return path.suffix.lower() in IMAGE_SUFFIXES


def _file_kind(path: Path) -> str:
    """파일의 종류 문구를 반환한다."""
    label = "동영상" if path.suffix.lower() in VIDEO_SUFFIXES else "이미지"
    return f"{path.suffix[1:].upper()} {label}"


def read_entry_details(path: Path, is_folder: bool) -> EntryDetails:
    """항목의 수정일, 크기, 종류를 읽는다. 폴더의 크기는 계산하지 않는다.

    읽는 사이 항목이 지워졌으면 오류 대신 수정일과 크기를 None으로 둔다.
    """
    kind = FOLDER_KIND if is_folder else _file_kind(path)
    try:
        stat = path.stat()
    except OSError:
        return EntryDetails(None, None, kind)
    size = None if is_folder else stat.st_size
    return EntryDetails(datetime.fromtimestamp(stat.st_mtime), size, kind)


def format_size(size: int | None) -> str:
    """파일 크기를 Finder처럼 1000 단위로 표시한다. 값이 없으면 --를 반환한다."""
    if size is None:
        return UNKNOWN_VALUE
    if size < 1000:
        return f"{size}바이트"
    value = float(size)
    unit = SIZE_UNITS[0]
    for unit in SIZE_UNITS:
        value /= 1000
        if value < 1000:
            break
    return f"{value:.1f} {unit}"


def format_modified(modified: datetime | None) -> str:
    """수정일을 'YYYY. M. D. 오전 H:MM' 형식으로 표시한다. 값이 없으면 --를 반환한다."""
    if modified is None:
        return UNKNOWN_VALUE
    period = "오전" if modified.hour < 12 else "오후"
    hour = modified.hour % 12 or 12
    return (
        f"{modified.year}. {modified.month}. {modified.day}. "
        f"{period} {hour}:{modified.minute:02}"
    )


def list_media_files(folder: Path) -> list[str]:
    """folder 바로 아래의 사진과 동영상 파일 이름을 정렬해 반환한다. 숨김 파일은 제외한다."""
    names = [
        entry.name
        for entry in folder.iterdir()
        if entry.is_file()
        and entry.suffix.lower() in MEDIA_SUFFIXES
        and not is_hidden(entry)
    ]
    return sorted(names, key=_sort_key)
