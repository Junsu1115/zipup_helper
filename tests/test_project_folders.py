"""core.project_folders 테스트. 실제 프로젝트 폴더 대신 임시 폴더만 사용한다."""

from pathlib import Path

import pytest

from core.project_folders import (
    SUBFOLDER_NAMES,
    FolderNameError,
    create_project_folder,
    list_project_folders,
)


def test_list_returns_sorted_visible_folders_only(tmp_path: Path) -> None:
    """파일과 숨김 폴더를 빼고 폴더 이름만 정렬해 반환하는지 확인한다."""
    for name in ("나", "가", "b", "A", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").touch()

    assert list_project_folders(tmp_path) == ["A", "b", "가", "나"]


def test_list_raises_when_root_is_missing(tmp_path: Path) -> None:
    """기준 폴더가 없으면 오류가 발생하는지 확인한다."""
    with pytest.raises(FileNotFoundError):
        list_project_folders(tmp_path / "missing")


def test_create_makes_folder_and_strips_spaces(tmp_path: Path) -> None:
    """앞뒤 공백을 정리한 이름으로 폴더를 만드는지 확인한다."""
    assert create_project_folder("  새 프로젝트  ", tmp_path) == "새 프로젝트"
    assert (tmp_path / "새 프로젝트").is_dir()


@pytest.mark.parametrize(
    "name",
    ["", "   ", ".", "..", ".hidden", "a/b", "../escape", "a\\b", "a:b"],
)
def test_create_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    """쓸 수 없는 이름을 거부하고 아무것도 만들지 않는지 확인한다."""
    with pytest.raises(FolderNameError):
        create_project_folder(name, tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_create_rejects_existing_folder(tmp_path: Path) -> None:
    """같은 이름의 폴더가 있으면 거부하는지 확인한다."""
    (tmp_path / "기존").mkdir()

    with pytest.raises(FolderNameError):
        create_project_folder("기존", tmp_path)


def test_create_without_subfolders_makes_only_project_folder(
    tmp_path: Path,
) -> None:
    """기본 동작에서는 이름으로 된 폴더만 만드는지 확인한다."""
    create_project_folder("프로젝트", tmp_path)

    assert list((tmp_path / "프로젝트").iterdir()) == []


def test_create_with_subfolders_makes_all_subfolders(tmp_path: Path) -> None:
    """선택하면 Document, Reference, Image, Video를 함께 만드는지 확인한다."""
    create_project_folder("프로젝트", tmp_path, with_subfolders=True)

    created = {entry.name for entry in (tmp_path / "프로젝트").iterdir()}
    assert created == {"Document", "Reference", "Image", "Video"}
    assert created == set(SUBFOLDER_NAMES)


def test_create_with_subfolders_keeps_existing_project_untouched(
    tmp_path: Path,
) -> None:
    """같은 이름의 프로젝트가 있으면 경고 문구를 내고 내용을 바꾸지 않는지 확인한다."""
    (tmp_path / "기존").mkdir()

    with pytest.raises(FolderNameError, match="'기존' 프로젝트가 이미 있습니다."):
        create_project_folder("기존", tmp_path, with_subfolders=True)

    assert list((tmp_path / "기존").iterdir()) == []


def test_create_keeps_partial_folders_when_subfolder_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """하위 폴더를 만들다 실패하면 이미 만든 폴더를 남기고 오류를 내는지 확인한다."""
    original_mkdir = Path.mkdir

    def failing_mkdir(path: Path) -> None:
        """Image 폴더를 만들 때만 권한 오류를 낸다."""
        if path.name == "Image":
            raise PermissionError("권한 없음")
        original_mkdir(path)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)

    with pytest.raises(PermissionError):
        create_project_folder("프로젝트", tmp_path, with_subfolders=True)

    created = {entry.name for entry in (tmp_path / "프로젝트").iterdir()}
    assert created == {"Document", "Reference"}


def test_create_raises_when_root_is_missing(tmp_path: Path) -> None:
    """기준 폴더가 없으면 자동으로 만들지 않고 오류가 발생하는지 확인한다."""
    root = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        create_project_folder("새 폴더", root)

    assert not root.exists()
