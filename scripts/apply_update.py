"""프로그램이 꺼진 뒤 파일을 새 버전으로 바꾸고 다시 켠다.

실행 중에는 프로그램 파일을 바꿀 수 없어서 별도 프로세스로 실행한다.
프로그램 패키지가 함께 교체되므로 파이썬 기본 모듈만 사용한다.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

# 사용자의 설정과 결과물, 가상환경은 새 버전으로 덮어쓰지 않는다.
PROTECTED_NAMES = frozenset({".venv", ".git", ".env", ".update", "outputs"})
WORK_FOLDER_NAME = ".update"
BACKUP_FOLDER_NAME = "backup"
FAILURE_MARKER_NAME = "failed.txt"
LOG_FILE_NAME = "update.log"
REQUIREMENTS_NAME = "requirements.txt"
EXIT_WAIT_SECONDS = 30.0
RENAME_ATTEMPTS = 5
RENAME_RETRY_SECONDS = 1.0
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_DETACHED_PROCESS = 0x00000008
WINDOWS_NEW_PROCESS_GROUP = 0x00000200


def _is_process_alive_windows(process_id: int) -> bool:
    """Windows에서 프로세스가 살아 있는지 반환한다."""
    import ctypes  # Windows 전용 호출에만 쓰므로 이곳에서 불러온다.
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(WINDOWS_SYNCHRONIZE, False, process_id)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


def is_process_alive(process_id: int) -> bool:
    """프로세스가 살아 있는지 반환한다."""
    if sys.platform == "win32":
        # Windows의 os.kill은 신호 0이어도 프로세스를 끝낼 수 있어 쓰지 않는다.
        return _is_process_alive_windows(process_id)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(
    process_id: int,
    timeout: float = EXIT_WAIT_SECONDS,
    is_alive: Callable[[int], bool] = is_process_alive,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """프로세스가 끝나기를 기다리고, 시간 안에 끝났는지 반환한다."""
    interval = 0.2
    waited = 0.0
    while is_alive(process_id):
        if waited >= timeout:
            return False
        sleep(interval)
        waited += interval
    return True


def _rename(source: Path, target: Path) -> None:
    """이름을 바꾼다. 백신 등이 잠시 파일을 잡고 있을 수 있어 몇 번 다시 시도한다."""
    for attempt in range(RENAME_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(RENAME_RETRY_SECONDS)


def _remove(path: Path) -> None:
    """파일이나 폴더를 지운다."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def replace_program_files(root: Path, staged: Path, backup: Path) -> None:
    """프로그램 파일을 새 버전으로 바꾼다. 중간에 실패하면 이전 상태로 되돌린다."""
    backup.mkdir(parents=True, exist_ok=True)
    backed_up: list[str] = []
    installed: list[str] = []
    try:
        for item in sorted(staged.iterdir()):
            if item.name in PROTECTED_NAMES:
                continue
            target = root / item.name
            if target.exists():
                _rename(target, backup / item.name)
                backed_up.append(item.name)
            _rename(item, target)
            installed.append(item.name)
    except OSError:
        for name in installed:
            _remove(root / name)
        for name in backed_up:
            _rename(backup / name, root / name)
        raise


def _pip_python(python: Path) -> Path:
    """pip를 실행할 파이썬을 반환한다. pythonw는 출력이 없어 pip를 실행하기에 맞지 않는다."""
    if python.name.lower() == "pythonw.exe":
        return python.with_name("python.exe")
    return python


def install_requirements_if_changed(
    root: Path,
    staged: Path,
    python: Path,
    run: Callable[..., object] = subprocess.run,
) -> bool:
    """필요한 패키지 목록이 바뀌었으면 설치하고, 설치했는지 반환한다."""
    current = root / REQUIREMENTS_NAME
    new = staged / REQUIREMENTS_NAME
    if current.exists() and current.read_bytes() == new.read_bytes():
        return False
    run(
        [
            str(_pip_python(python)),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(new),
        ],
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return True


def relaunch(
    root: Path, python: Path, popen: Callable[..., object] = subprocess.Popen
) -> None:
    """프로그램을 이 프로세스와 분리해서 다시 켠다."""
    if sys.platform == "win32":
        options = {
            "creationflags": WINDOWS_DETACHED_PROCESS | WINDOWS_NEW_PROCESS_GROUP
        }
    else:
        options = {"start_new_session": True}
    popen(
        [str(python), str(root / "main.py")],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **options,
    )


def apply_update(root: Path, staged: Path, python: Path) -> None:
    """패키지를 설치하고 파일을 바꾼 뒤 준비 폴더를 지운다."""
    work = root / WORK_FOLDER_NAME
    backup = work / BACKUP_FOLDER_NAME
    install_requirements_if_changed(root, staged, python)
    replace_program_files(root, staged, backup)
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)


def main(arguments: list[str] | None = None) -> int:
    """프로그램이 꺼지기를 기다려 업데이트하고, 결과와 상관없이 다시 켠다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("staged", type=Path)
    parser.add_argument("process_id", type=int)
    parsed = parser.parse_args(arguments)

    work = parsed.root / WORK_FOLDER_NAME
    logging.basicConfig(
        filename=work / LOG_FILE_NAME,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    python = Path(sys.executable)
    if not wait_for_exit(parsed.process_id):
        logging.error("프로그램이 꺼지지 않아 업데이트를 취소했습니다.")
        (work / FAILURE_MARKER_NAME).touch()
        return 1

    exit_code = 0
    try:
        apply_update(parsed.root, parsed.staged, python)
        logging.info("업데이트를 마쳤습니다.")
    except subprocess.CalledProcessError as error:
        logging.error("패키지 설치 실패: %s", error.stderr.decode(errors="replace"))
        (work / FAILURE_MARKER_NAME).touch()
        exit_code = 1
    except OSError:
        logging.exception("파일 교체 실패")
        (work / FAILURE_MARKER_NAME).touch()
        exit_code = 1
    relaunch(parsed.root, python)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
