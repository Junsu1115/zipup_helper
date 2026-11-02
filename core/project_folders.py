"""프로젝트 폴더의 목록 조회와 생성."""

import re
import stat
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path.home() / "Project"
# 어느 운영체제에서 만든 폴더든 양쪽에서 쓸 수 있도록 Windows 규칙까지 함께 검사한다.
FORBIDDEN_CHARACTERS = '/\\:*?"<>|\0'
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)
HIDDEN_FILE_ATTRIBUTES = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM
# 선택할 수 있는 하위 폴더와, 그 폴더를 만들 때 함께 만드는 폴더들.
SUBFOLDER_TREE: dict[str, tuple[str, ...]] = {
    "Document": ("PPT_ORG", "PPT_Break"),
    "Reference": (),
    "Work": ("Image", "Video"),
    "Pub":("Image", "Video")
}


class FolderNameError(ValueError):
    """폴더 이름을 쓸 수 없을 때 발생하는 오류. 메시지는 사용자에게 그대로 보여준다."""


def _sort_key(name: str) -> tuple[tuple[str | int, ...], str]:
    """이름 정렬 기준을 반환한다.

    macOS의 한글 자모 분리 표기와 대소문자 차이를 무시하고, 이름 속 숫자는 크기순으로
    비교한다. 값이 같은 이름(001과 1)은 원래 이름으로 순서를 고정한다.
    """
    normalized = unicodedata.normalize("NFC", name).casefold()
    # 분리 결과는 문자열과 숫자가 번갈아 나오므로, 같은 위치끼리 비교할 수 있다.
    parts = re.split(r"(\d+)", normalized)
    return (
        tuple(
            int(part) if index % 2 else part for index, part in enumerate(parts)
        ),
        name,
    )


def is_hidden(entry: Path) -> bool:
    """숨김 항목인지 반환한다. '.'으로 시작하는 이름과 Windows의 숨김, 시스템 항목이 해당한다."""
    if entry.name.startswith("."):
        return True
    try:
        attributes = getattr(entry.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & HIDDEN_FILE_ATTRIBUTES)


def list_project_folders(root: Path = PROJECT_ROOT) -> list[str]:
    """root 바로 아래의 폴더 이름을 정렬해 반환한다. 파일과 숨김 폴더는 제외한다."""
    names = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and not is_hidden(entry)
    ]
    return sorted(names, key=_sort_key)


def _validate_folder_name(name: str) -> None:
    """폴더 이름을 검사하고, 쓸 수 없는 이름이면 FolderNameError를 발생시킨다."""
    if not name:
        raise FolderNameError("폴더 이름을 입력해 주세요.")
    if name.startswith("."):
        raise FolderNameError("폴더 이름은 '.'으로 시작할 수 없습니다.")
    if any(character in name for character in FORBIDDEN_CHARACTERS):
        raise FolderNameError('폴더 이름에는 / \\ : * ? " < > | 를 쓸 수 없습니다.')
    if name.endswith("."):
        raise FolderNameError("폴더 이름은 '.'으로 끝날 수 없습니다.")
    if name.split(".")[0].upper() in WINDOWS_RESERVED_NAMES:
        raise FolderNameError(f"'{name}'은 Windows에서 예약된 이름이라 쓸 수 없습니다.")


def clean_folder_name(name: str) -> str:
    """앞뒤 공백을 정리한 이름을 반환한다. 쓸 수 없으면 FolderNameError를 발생시킨다."""
    cleaned_name = name.strip()
    _validate_folder_name(cleaned_name)
    return cleaned_name


def is_same_folder_name(first: str, second: str) -> bool:
    """대소문자와 한글 표기 방식을 무시하고 같은 이름인지 반환한다."""
    normalize = lambda name: unicodedata.normalize("NFC", name).casefold()  # noqa: E731
    return normalize(first) == normalize(second)


@dataclass
class FolderCreationSummary:
    """여러 폴더를 만든 결과."""

    created_count: int = 0
    failures: list[str] = field(default_factory=list)


def create_folders(names: Iterable[str], parent: Path) -> FolderCreationSummary:
    """parent 안에 폴더들을 만든다. 하나가 실패해도 나머지는 계속 만들고, 만든 폴더는 그대로 둔다."""
    summary = FolderCreationSummary()
    for name in names:
        try:
            (parent / clean_folder_name(name)).mkdir()
        except FolderNameError as error:
            summary.failures.append(str(error))
        except FileExistsError:
            summary.failures.append(f"'{name}': 이 위치에 이미 있습니다.")
        except OSError as error:
            summary.failures.append(f"'{name}': {error}")
        else:
            summary.created_count += 1
    return summary


def create_project_folder(
    name: str,
    root: Path = PROJECT_ROOT,
    subfolders: Iterable[str] = (),
) -> str:
    """root 안에 새 폴더를 만들고, 앞뒤 공백을 정리한 실제 폴더 이름을 반환한다.

    subfolders는 SUBFOLDER_TREE의 폴더 이름들이며, 새 폴더 안에 그 폴더와
    딸린 폴더들을 함께 만든다.
    만드는 도중 실패하면 이미 만든 폴더는 지우지 않고 OSError를 그대로 발생시킨다.
    """
    cleaned_name = name.strip()
    _validate_folder_name(cleaned_name)
    project_folder = root / cleaned_name
    try:
        project_folder.mkdir()
    except FileExistsError as error:
        raise FolderNameError(
            f"'{cleaned_name}' 프로젝트가 이미 있습니다."
        ) from error
    for subfolder_name in subfolders:
        subfolder = project_folder / subfolder_name
        subfolder.mkdir()
        for child_name in SUBFOLDER_TREE[subfolder_name]:
            (subfolder / child_name).mkdir()
    return cleaned_name


def _subfolder_paths(project: Path) -> Iterator[Path]:
    """SUBFOLDER_TREE의 폴더 경로를 상위 폴더부터 차례로 반환한다."""
    for subfolder_name, child_names in SUBFOLDER_TREE.items():
        subfolder = project / subfolder_name
        yield subfolder
        for child_name in child_names:
            yield subfolder / child_name


def has_missing_subfolders(project: Path) -> bool:
    """SUBFOLDER_TREE 중 project에 없는 폴더가 하나라도 있는지 반환한다."""
    return any(not folder.is_dir() for folder in _subfolder_paths(project))


def add_missing_subfolders(project: Path) -> int:
    """SUBFOLDER_TREE 중 project에 없는 폴더만 만들고, 만든 폴더 수를 반환한다.

    이미 있는 폴더는 건드리지 않는다. 같은 이름의 파일이 있거나 만드는 도중 실패하면
    이미 만든 폴더는 지우지 않고 OSError를 그대로 발생시킨다.
    """
    created_count = 0
    for folder in _subfolder_paths(project):
        if not folder.is_dir():
            folder.mkdir()
            created_count += 1
    return created_count
