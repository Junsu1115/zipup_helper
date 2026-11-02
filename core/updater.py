"""새 버전을 확인하고, 내려받은 압축 파일을 교체 직전 상태로 준비한다."""

import io
import json
import re
import shutil
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

REPOSITORY = "Junsu1115/zipup_helper"
LATEST_COMMIT_URL = f"https://api.github.com/repos/{REPOSITORY}/commits/main"
PROGRAM_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = Path("core") / "app_version.txt"
WORK_FOLDER_NAME = ".update"
STAGED_FOLDER_NAME = "new"
APPLY_SCRIPT = Path("scripts") / "apply_update.py"
FAILURE_MARKER_NAME = "failed.txt"
REQUIRED_FILES = (Path("main.py"), Path("requirements.txt"), APPLY_SCRIPT, VERSION_FILE)
VERSION_PATTERN = re.compile(r"[0-9a-f]{40}")


class UpdateError(Exception):
    """새 버전을 확인하거나 준비하지 못했을 때 발생하는 오류."""


def archive_url(version: str) -> str:
    """해당 버전의 압축 파일 주소를 반환한다."""
    return f"https://codeload.github.com/{REPOSITORY}/zip/{version}"


def read_version(root: Path) -> str | None:
    """폴더에 기록된 버전을 반환한다. 커밋 번호가 채워져 있지 않으면 None을 반환한다."""
    try:
        text = (root / VERSION_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text if VERSION_PATTERN.fullmatch(text) else None


def installed_version(root: Path = PROGRAM_ROOT) -> str | None:
    """업데이트할 수 있는 설치본의 버전을 반환한다.

    개발 중인 Git 폴더와 PyInstaller 실행 파일은 덮어쓰면 안 되므로 None을 반환한다.
    """
    if (root / ".git").exists() or getattr(sys, "frozen", False):
        return None
    return read_version(root)


def find_new_version(current: str, fetch: Callable[[str], bytes]) -> str | None:
    """GitHub의 최신 버전이 current와 다르면 그 버전을 반환한다."""
    try:
        latest = json.loads(fetch(LATEST_COMMIT_URL))["sha"]
    except (ValueError, KeyError, TypeError) as error:
        raise UpdateError("최신 버전 정보를 읽지 못했습니다.") from error
    if not isinstance(latest, str) or not VERSION_PATTERN.fullmatch(latest):
        raise UpdateError("최신 버전 정보를 읽지 못했습니다.")
    return None if latest == current else latest


def _extract_keeping_permissions(archive: zipfile.ZipFile, folder: Path) -> None:
    """압축을 풀고, 압축 파일에 기록된 실행 권한을 되살린다."""
    for info in archive.infolist():
        path = Path(archive.extract(info, folder))
        mode = info.external_attr >> 16
        if mode & 0o111 and not info.is_dir():
            path.chmod(path.stat().st_mode | (mode & 0o111))


def _extract_program(data: bytes, work: Path) -> Path:
    """압축 파일을 풀어 준비 폴더로 옮기고 그 경로를 반환한다."""
    extracted = work / "extracted"
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            tops = {name.split("/")[0] for name in archive.namelist()}
            if len(tops) != 1:
                raise UpdateError("내려받은 파일의 구성이 올바르지 않습니다.")
            _extract_keeping_permissions(archive, extracted)
    except zipfile.BadZipFile as error:
        raise UpdateError("내려받은 파일이 손상되었습니다.") from error
    staged = work / STAGED_FOLDER_NAME
    (extracted / tops.pop()).rename(staged)
    extracted.rmdir()
    return staged


def stage_update(data: bytes, version: str, root: Path = PROGRAM_ROOT) -> Path:
    """압축 파일을 검사하고 교체 직전 상태로 준비한다. 프로그램 파일은 아직 바꾸지 않는다."""
    work = root / WORK_FOLDER_NAME
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    staged = _extract_program(data, work)
    if not all((staged / path).is_file() for path in REQUIRED_FILES):
        raise UpdateError("내려받은 파일에 필요한 내용이 없습니다.")
    if read_version(staged) != version:
        raise UpdateError("내려받은 파일의 버전이 맞지 않습니다.")
    # 교체되는 폴더 밖에서 실행되도록 교체 스크립트를 따로 복사한다.
    shutil.copy2(staged / APPLY_SCRIPT, work / APPLY_SCRIPT.name)
    return staged


def apply_command(
    staged: Path, process_id: int, root: Path = PROGRAM_ROOT
) -> tuple[str, list[str]]:
    """프로그램이 꺼진 뒤 파일을 교체할 명령을 실행 파일과 인자로 나누어 반환한다."""
    script = root / WORK_FOLDER_NAME / APPLY_SCRIPT.name
    return sys.executable, [str(script), str(root), str(staged), str(process_id)]


def take_update_failure(root: Path = PROGRAM_ROOT) -> bool:
    """지난 업데이트가 실패했는지 반환하고, 한 번만 알리도록 기록을 지운다."""
    marker = root / WORK_FOLDER_NAME / FAILURE_MARKER_NAME
    if not marker.exists():
        return False
    marker.unlink(missing_ok=True)
    return True
