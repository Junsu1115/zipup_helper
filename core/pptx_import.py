"""PPTX 원본을 프로젝트에 저장하고, 안의 media 폴더를 꺼낸다."""

import shutil
import zipfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DOCUMENT_FOLDER = "Document"
ORIGINAL_FOLDER = "PPT_ORG"
BREAK_FOLDER = "PPT_Break"
MEDIA_FOLDER = "media"
PPTX_MEDIA_PREFIX = "ppt/media/"
PPTX_SUFFIX = ".pptx"
LEGACY_PPT_SUFFIX = ".ppt"


class PptxImportError(Exception):
    """PPTX를 처리하지 못했을 때 발생하는 오류. 메시지는 사용자에게 그대로 보여준다."""


@dataclass(frozen=True)
class DroppedFiles:
    """끌어다 놓은 파일을 종류별로 나눈 결과."""

    pptx: list[Path] = field(default_factory=list)
    legacy_ppt: list[Path] = field(default_factory=list)
    unsupported: list[Path] = field(default_factory=list)


@dataclass
class PptxImportSummary:
    """여러 PPTX를 처리한 결과."""

    imported_count: int = 0
    media_count: int = 0
    failures: list[str] = field(default_factory=list)


def classify_dropped_files(paths: Iterable[Path]) -> DroppedFiles:
    """파일을 확장자에 따라 PPTX, 구형 PPT, 지원하지 않는 파일로 나눈다."""
    dropped = DroppedFiles()
    for path in paths:
        suffix = path.suffix.lower()
        if suffix == PPTX_SUFFIX and path.is_file():
            dropped.pptx.append(path)
        elif suffix == LEGACY_PPT_SUFFIX:
            dropped.legacy_ppt.append(path)
        else:
            dropped.unsupported.append(path)
    return dropped


def _media_file_name(member_name: str) -> str | None:
    """압축 파일 안의 경로에서 파일 이름만 꺼낸다. 폴더 밖에 쓰지 못하게 경로는 버린다."""
    name = PurePosixPath(member_name.replace("\\", "/")).name
    return None if name in ("", ".", "..") else name


def _extract_media(pptx: Path, break_folder: Path) -> int:
    """PPTX 안의 media 파일을 break_folder/media에 꺼내고, 꺼낸 파일 수를 반환한다."""
    media_folder = break_folder / MEDIA_FOLDER
    try:
        with zipfile.ZipFile(pptx) as archive:
            members = [
                (member, _media_file_name(member.filename))
                for member in archive.infolist()
                if member.filename.startswith(PPTX_MEDIA_PREFIX)
                and not member.is_dir()
            ]
            members = [(member, name) for member, name in members if name]
            if not members:
                return 0
            if media_folder.exists():
                raise PptxImportError(
                    f"'{pptx.name}': 원본은 저장했지만, {BREAK_FOLDER}/{MEDIA_FOLDER} "
                    "폴더가 이미 있어 사진을 꺼내지 않았습니다."
                )
            media_folder.mkdir()
            for member, name in members:
                with archive.open(member) as source, (media_folder / name).open(
                    "xb"
                ) as target:
                    shutil.copyfileobj(source, target)
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        zlib.error,
    ) as error:
        raise PptxImportError(
            f"'{pptx.name}': 원본은 저장했지만, 파일이 손상되었거나 암호가 걸려 있어 "
            "사진을 꺼내지 못했습니다."
        ) from error
    return len(members)


def import_pptx(source: Path, project: Path) -> int:
    """원본을 PPT_ORG에 복사한 뒤 media를 PPT_Break에 꺼내고, 꺼낸 파일 수를 반환한다.

    끌어다 놓은 원본은 바꾸지 않는다. PPT_ORG에 같은 이름의 파일이 있으면 덮어쓰지 않고
    PptxImportError를 발생시킨다.
    """
    document_folder = project / DOCUMENT_FOLDER
    original_folder = document_folder / ORIGINAL_FOLDER
    break_folder = document_folder / BREAK_FOLDER
    destination = original_folder / source.name

    if destination.exists():
        raise PptxImportError(
            f"'{source.name}': {ORIGINAL_FOLDER}에 같은 이름의 파일이 있어 건너뛰었습니다."
        )

    original_folder.mkdir(parents=True, exist_ok=True)
    break_folder.mkdir(exist_ok=True)
    shutil.copy2(source, destination)
    return _extract_media(destination, break_folder)


def import_pptx_files(sources: Iterable[Path], project: Path) -> PptxImportSummary:
    """PPTX를 차례로 처리한다. 한 파일이 실패해도 나머지는 계속 처리한다."""
    summary = PptxImportSummary()
    for source in sources:
        try:
            summary.media_count += import_pptx(source, project)
            summary.imported_count += 1
        except PptxImportError as error:
            summary.failures.append(str(error))
        except OSError as error:
            summary.failures.append(f"'{source.name}': {error}")
    return summary
