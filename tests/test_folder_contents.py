"""core.folder_contents 테스트. 임시 폴더만 사용한다."""

from datetime import datetime
from pathlib import Path

import pytest

from core.folder_contents import (
    format_modified,
    format_size,
    list_media_files,
    read_entry_details,
)


def test_lists_only_visible_images_sorted(tmp_path: Path) -> None:
    """사진 파일만 정렬해 반환하고, 폴더와 숨김 파일과 다른 파일은 빠지는지 확인한다."""
    for name in ("나.png", "가.JPG", "다.emf", ".숨김.png", "문서.pptx", "메모.txt"):
        (tmp_path / name).touch()
    (tmp_path / "폴더.png").mkdir()

    assert list_media_files(tmp_path) == ["가.JPG", "나.png", "다.emf"]


def test_read_details_of_image(tmp_path: Path) -> None:
    """사진 파일의 크기, 수정일, 종류를 읽는지 확인한다."""
    image = tmp_path / "사진.Jpg"
    image.write_bytes(b"12345")

    details = read_entry_details(image, is_folder=False)

    assert details.size == 5
    assert details.modified is not None
    assert details.kind == "JPG 이미지"


def test_read_details_of_folder_has_no_size(tmp_path: Path) -> None:
    """폴더의 크기는 비어 있고 종류가 폴더인지 확인한다."""
    details = read_entry_details(tmp_path, is_folder=True)

    assert details.size is None
    assert details.kind == "폴더"


def test_read_details_of_deleted_entry_has_no_values(tmp_path: Path) -> None:
    """읽는 사이 지워진 항목도 오류 없이 처리하는지 확인한다."""
    details = read_entry_details(tmp_path / "없음.png", is_folder=False)

    assert details.modified is None
    assert details.size is None


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (None, "--"),
        (0, "0바이트"),
        (999, "999바이트"),
        (1000, "1.0 KB"),
        (1_500_000, "1.5 MB"),
        (2_300_000_000, "2.3 GB"),
    ],
)
def test_format_size(size: int | None, expected: str) -> None:
    """파일 크기가 1000 단위로 표시되는지 확인한다."""
    assert format_size(size) == expected


@pytest.mark.parametrize(
    ("modified", "expected"),
    [
        (None, "--"),
        (datetime(2026, 10, 30, 0, 5), "2026. 10. 30. 오전 12:05"),
        (datetime(2026, 1, 2, 9, 30), "2026. 1. 2. 오전 9:30"),
        (datetime(2026, 1, 2, 12, 0), "2026. 1. 2. 오후 12:00"),
        (datetime(2026, 1, 2, 15, 7), "2026. 1. 2. 오후 3:07"),
    ],
)
def test_format_modified(modified: datetime | None, expected: str) -> None:
    """수정일이 오전과 오후로 나뉘어 표시되는지 확인한다."""
    assert format_modified(modified) == expected


def test_lists_video_files(tmp_path: Path) -> None:
    """동영상 파일도 목록에 포함되는지 확인한다."""
    for name in ("a.mp4", "b.MOV", "c.m4v", "d.avi", "e.mkv", "f.webm", "g.mp3"):
        (tmp_path / name).touch()

    assert list_media_files(tmp_path) == [
        "a.mp4",
        "b.MOV",
        "c.m4v",
        "d.avi",
        "e.mkv",
        "f.webm",
    ]


def test_read_details_of_video(tmp_path: Path) -> None:
    """동영상의 종류 문구가 맞는지 확인한다."""
    video = tmp_path / "영상.Mp4"
    video.write_bytes(b"1")

    assert read_entry_details(video, is_folder=False).kind == "MP4 동영상"


def test_raises_when_folder_is_missing(tmp_path: Path) -> None:
    """폴더가 없으면 오류가 발생하는지 확인한다."""
    with pytest.raises(FileNotFoundError):
        list_media_files(tmp_path / "missing")
