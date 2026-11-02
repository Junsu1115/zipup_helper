"""Work, Reference 폴더 안에서 파일과 폴더를 옮긴다."""

import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from core.photo_copy import drop_target_containing


@dataclass
class FileMoveSummary:
    """여러 항목을 옮긴 결과."""

    moved_count: int = 0
    existing_count: int = 0
    failures: list[str] = field(default_factory=list)


def _move_one(source: Path, destination: Path, summary: FileMoveSummary) -> None:
    """항목 하나를 destination으로 옮기고 결과를 summary에 기록한다."""
    if source.parent == destination:
        return
    if source == destination or source in destination.parents:
        summary.failures.append(f"'{source.name}': 폴더를 자기 안으로 옮길 수 없습니다.")
        return

    target = destination / source.name
    if target.exists():
        summary.existing_count += 1
        return
    try:
        shutil.move(source, target)
    except OSError as error:
        summary.failures.append(f"'{source.name}': {error}")
    else:
        summary.moved_count += 1


def move_within_target(
    sources: Iterable[Path], destination: Path, project: Path
) -> FileMoveSummary:
    """항목을 같은 Work 또는 Reference 안의 폴더로 옮긴다.

    다른 폴더에서 온 항목은 옮기지 않는다. 같은 이름이 있으면 덮어쓰지 않고 그대로 둔다.
    """
    summary = FileMoveSummary()
    target_name = drop_target_containing(destination, project)
    for source in sources:
        source_target = drop_target_containing(source.parent, project)
        if target_name is None or source_target != target_name:
            summary.failures.append(
                f"'{source.name}': 같은 폴더 안에서만 옮길 수 있습니다."
            )
        else:
            _move_one(source, destination, summary)
    return summary
