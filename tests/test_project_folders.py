"""core.project_folders 테스트. 실제 프로젝트 폴더 대신 임시 폴더만 사용한다."""

import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.project_folders import (
    PROJECT_ROOT,
    SUBFOLDER_TREE,
    FolderNameError,
    add_missing_subfolders,
    create_folders,
    create_project_folder,
    has_missing_subfolders,
    is_hidden,
    list_project_folders,
)


def test_list_returns_sorted_visible_folders_only(tmp_path: Path) -> None:
    """파일과 숨김 폴더를 빼고 폴더 이름만 정렬해 반환하는지 확인한다."""
    for name in ("나", "가", "b", "A", ".hidden"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").touch()

    assert list_project_folders(tmp_path) == ["A", "b", "가", "나"]


def test_default_root_is_project_folder_in_home() -> None:
    """기본 폴더가 사용자 홈 폴더의 Project인지 확인한다."""
    assert PROJECT_ROOT == Path.home() / "Project"


def test_create_allows_similar_to_reserved_names(tmp_path: Path) -> None:
    """예약된 이름과 비슷하기만 한 이름은 만들 수 있는지 확인한다."""
    for name in ("CONTENT", "com10", "프로젝트.v1"):
        assert create_project_folder(name, tmp_path) == name


@pytest.mark.parametrize(
    "attribute",
    [stat.FILE_ATTRIBUTE_HIDDEN, stat.FILE_ATTRIBUTE_SYSTEM],
)
def test_is_hidden_detects_windows_attributes(
    tmp_path: Path, attribute: int
) -> None:
    """Windows의 숨김, 시스템 속성이 있는 항목을 숨김으로 보는지 확인한다."""
    fake_stat = SimpleNamespace(st_file_attributes=attribute)

    with patch.object(Path, "stat", return_value=fake_stat):
        assert is_hidden(tmp_path / "$RECYCLE.BIN")


def test_is_hidden_handles_normal_and_missing_entries(tmp_path: Path) -> None:
    """일반 항목과 사라진 항목은 숨김이 아니고, 점으로 시작하면 숨김인지 확인한다."""
    (tmp_path / "보통").mkdir()

    assert not is_hidden(tmp_path / "보통")
    assert not is_hidden(tmp_path / "없음")
    assert is_hidden(tmp_path / ".숨김")


def test_list_sorts_numbers_by_value(tmp_path: Path) -> None:
    """이름 속 숫자를 크기순으로 정렬하고, 값이 같아도 순서가 고정되는지 확인한다."""
    for name in ("image10", "image2", "image1", "프로젝트10", "프로젝트2", "a01", "a1"):
        (tmp_path / name).mkdir()

    assert list_project_folders(tmp_path) == [
        "a01",
        "a1",
        "image1",
        "image2",
        "image10",
        "프로젝트2",
        "프로젝트10",
    ]


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
    [
        "",
        "   ",
        ".",
        "..",
        ".hidden",
        "a/b",
        "../escape",
        "a\\b",
        "a:b",
        "a*b",
        "a?b",
        'a"b',
        "a<b",
        "a>b",
        "a|b",
        "끝점.",
        "CON",
        "con",
        "nul.txt",
        "COM1",
        "lpt9",
    ],
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


def test_create_with_all_subfolders(tmp_path: Path) -> None:
    """전체를 선택하면 4개 폴더와 각 폴더 아래 폴더까지 만드는지 확인한다."""
    create_project_folder("프로젝트", tmp_path, SUBFOLDER_TREE)

    project = tmp_path / "프로젝트"
    created = {entry.name for entry in project.iterdir()}
    assert created == {"Document", "Reference", "Work", "Pub"}
    for name, child_names in SUBFOLDER_TREE.items():
        children = {entry.name for entry in (project / name).iterdir()}
        assert children == set(child_names)


def test_create_makes_only_selected_subfolders(tmp_path: Path) -> None:
    """선택한 폴더만 만들고, Document가 없으면 PPT 폴더도 만들지 않는지 확인한다."""
    create_project_folder("프로젝트", tmp_path, ["Reference", "Work"])

    project = tmp_path / "프로젝트"
    assert {entry.name for entry in project.iterdir()} == {"Reference", "Work"}
    assert list((project / "Reference").iterdir()) == []
    assert {entry.name for entry in (project / "Work").iterdir()} == {"Image", "Video"}


def test_create_with_subfolders_keeps_existing_project_untouched(
    tmp_path: Path,
) -> None:
    """같은 이름의 프로젝트가 있으면 경고 문구를 내고 내용을 바꾸지 않는지 확인한다."""
    (tmp_path / "기존").mkdir()

    with pytest.raises(FolderNameError, match="'기존' 프로젝트가 이미 있습니다."):
        create_project_folder("기존", tmp_path, SUBFOLDER_TREE)

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
        create_project_folder("프로젝트", tmp_path, SUBFOLDER_TREE)

    created = {entry.name for entry in (tmp_path / "프로젝트").iterdir()}
    assert created == {"Document", "Reference", "Work"}
    assert list((tmp_path / "프로젝트" / "Work").iterdir()) == []


def test_create_folders_makes_all_and_counts(tmp_path: Path) -> None:
    """이름 목록의 폴더를 모두 만들고 개수를 알려 주는지 확인한다."""
    summary = create_folders(["시안1", "  시안2  "], tmp_path)

    assert summary.created_count == 2
    assert summary.failures == []
    assert {entry.name for entry in tmp_path.iterdir()} == {"시안1", "시안2"}


def test_create_folders_continues_after_failures(tmp_path: Path) -> None:
    """이미 있거나 잘못된 이름은 알리고, 나머지는 계속 만드는지 확인한다."""
    (tmp_path / "기존").mkdir()
    (tmp_path / "기존" / "내용").mkdir()

    summary = create_folders(["기존", "a/b", "새폴더"], tmp_path)

    assert summary.created_count == 1
    assert len(summary.failures) == 2
    assert (tmp_path / "새폴더").is_dir()
    assert (tmp_path / "기존" / "내용").is_dir()


def test_has_missing_reflects_structure(tmp_path: Path) -> None:
    """폴더가 빠졌는지를 안쪽 폴더까지 정확히 알려 주는지 확인한다."""
    assert has_missing_subfolders(tmp_path)

    add_missing_subfolders(tmp_path)
    assert not has_missing_subfolders(tmp_path)

    (tmp_path / "Work" / "Video").rmdir()
    assert has_missing_subfolders(tmp_path)


def test_has_missing_treats_same_name_file_as_missing(tmp_path: Path) -> None:
    """같은 이름의 파일은 폴더가 없는 것으로 보는지 확인한다."""
    add_missing_subfolders(tmp_path)
    (tmp_path / "Reference").rmdir()
    (tmp_path / "Reference").write_text("파일")

    assert has_missing_subfolders(tmp_path)


def test_add_missing_creates_whole_structure(tmp_path: Path) -> None:
    """빈 프로젝트에 하위 폴더 구조 전체를 만들고 만든 수를 반환하는지 확인한다."""
    created_count = add_missing_subfolders(tmp_path)

    expected = sum(1 + len(children) for children in SUBFOLDER_TREE.values())
    assert created_count == expected
    for name, child_names in SUBFOLDER_TREE.items():
        children = {entry.name for entry in (tmp_path / name).iterdir()}
        assert children == set(child_names)


def test_add_missing_keeps_existing_contents(tmp_path: Path) -> None:
    """이미 있는 폴더와 파일은 그대로 두고, 없는 폴더만 만드는지 확인한다."""
    document = tmp_path / "Document"
    (document / "PPT_ORG").mkdir(parents=True)
    keep_file = document / "PPT_ORG" / "보관.pptx"
    keep_file.write_text("내용")
    (tmp_path / "내폴더").mkdir()

    add_missing_subfolders(tmp_path)

    assert keep_file.read_text() == "내용"
    assert (tmp_path / "내폴더").is_dir()
    assert (document / "PPT_Break").is_dir()
    assert (tmp_path / "Work" / "Image").is_dir()


def test_add_missing_twice_creates_nothing(tmp_path: Path) -> None:
    """모두 있으면 아무것도 만들지 않고 0을 반환하는지 확인한다."""
    add_missing_subfolders(tmp_path)

    assert add_missing_subfolders(tmp_path) == 0


def test_add_missing_raises_when_file_has_same_name(tmp_path: Path) -> None:
    """같은 이름의 파일이 있으면 오류를 내고 파일을 건드리지 않는지 확인한다."""
    blocking_file = tmp_path / "Work"
    blocking_file.write_text("파일")

    with pytest.raises(OSError):
        add_missing_subfolders(tmp_path)

    assert blocking_file.read_text() == "파일"
    assert (tmp_path / "Document").is_dir()


def test_create_raises_when_root_is_missing(tmp_path: Path) -> None:
    """기준 폴더가 없으면 자동으로 만들지 않고 오류가 발생하는지 확인한다."""
    root = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        create_project_folder("새 폴더", root)

    assert not root.exists()
