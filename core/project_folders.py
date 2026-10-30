"""프로젝트 폴더의 목록 조회와 생성."""

import unicodedata
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path("/Users/zipup/Project")
FORBIDDEN_CHARACTERS = "/\\:\0"
# 선택할 수 있는 하위 폴더와, 그 폴더를 만들 때 함께 만드는 폴더들.
SUBFOLDER_TREE: dict[str, tuple[str, ...]] = {
    "Document": ("PPT_ORG", "PPT_Break"),
    "Reference": (),
    "Work": ("Image", "Video"),
    "Pub":("Image", "Video")
}


class FolderNameError(ValueError):
    """폴더 이름을 쓸 수 없을 때 발생하는 오류. 메시지는 사용자에게 그대로 보여준다."""


def _sort_key(name: str) -> str:
    """macOS의 한글 자모 분리 표기와 대소문자 차이를 무시하는 정렬 기준을 반환한다."""
    return unicodedata.normalize("NFC", name).casefold()


def list_project_folders(root: Path = PROJECT_ROOT) -> list[str]:
    """root 바로 아래의 폴더 이름을 정렬해 반환한다. 파일과 숨김 폴더는 제외한다."""
    names = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    return sorted(names, key=_sort_key)


def _validate_folder_name(name: str) -> None:
    """폴더 이름을 검사하고, 쓸 수 없는 이름이면 FolderNameError를 발생시킨다."""
    if not name:
        raise FolderNameError("폴더 이름을 입력해 주세요.")
    if name.startswith("."):
        raise FolderNameError("폴더 이름은 '.'으로 시작할 수 없습니다.")
    if any(character in name for character in FORBIDDEN_CHARACTERS):
        raise FolderNameError("폴더 이름에는 / \\ : 를 쓸 수 없습니다.")


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
