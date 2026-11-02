"""helper 명령과 바탕화면 바로가기를 등록한다. 설치 파일이 마지막에 실행한다."""

import ctypes
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

APP_NAME = "zipup_helper"
HELPER_NAME = "helper"
ZSHRC_MARKER = "# zipup_helper helper 명령"
PROJECT_FOLDER_NAME = "Project"
WINDOWS_ENVIRONMENT_KEY = "Environment"
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def ensure_project_folder(home: Path) -> bool:
    """홈 폴더에 Project 폴더가 없으면 만들고, 새로 만들었는지 반환한다."""
    folder = home / PROJECT_FOLDER_NAME
    if folder.exists():
        return False
    folder.mkdir(parents=True)
    return True


def _write_executable(path: Path, text: str) -> None:
    """실행 권한이 있는 파일을 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _mac_launch_script(root: Path) -> str:
    """터미널과 분리해 프로그램을 켜는 셸 스크립트를 만든다."""
    python = root / ".venv" / "bin" / "python"
    main = root / "main.py"
    return (
        "#!/bin/bash\n"
        f'cd "{root}" || exit 1\n'
        f'nohup "{python}" "{main}" >/dev/null 2>&1 &\n'
    )


def write_mac_helper(root: Path, home: Path) -> Path:
    """~/.local/bin에 helper 명령을 만들고 경로를 반환한다."""
    path = home / ".local" / "bin" / HELPER_NAME
    _write_executable(path, _mac_launch_script(root))
    return path


def ensure_zshrc_path(home: Path) -> bool:
    """~/.zshrc에 helper 폴더 경로를 추가한다. 이미 있으면 그대로 두고 False를 반환한다."""
    zshrc = home / ".zshrc"
    current = zshrc.read_text(encoding="utf-8") if zshrc.exists() else ""
    if ZSHRC_MARKER in current:
        return False
    separator = "" if not current or current.endswith("\n") else "\n"
    addition = f'{separator}\n{ZSHRC_MARKER}\nexport PATH="$HOME/.local/bin:$PATH"\n'
    zshrc.write_text(current + addition, encoding="utf-8")
    return True


def _mac_info_plist() -> str:
    """Mac 앱 묶음의 설정 내용을 만든다."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.zipup.helper.launcher</string>
    <key>CFBundleExecutable</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
"""


def write_mac_shortcut(root: Path, desktop: Path) -> Path:
    """바탕화면에 더블클릭으로 켜는 앱 묶음을 만들고 경로를 반환한다."""
    app = desktop / f"{APP_NAME}.app"
    contents = app / "Contents"
    _write_executable(contents / "MacOS" / APP_NAME, _mac_launch_script(root))
    (contents / "Info.plist").write_text(_mac_info_plist(), encoding="utf-8")
    resources = contents / "Resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "icon.icns").write_bytes((root / "assets" / "icon.icns").read_bytes())
    return app


def windows_helper_text(root: Path) -> str:
    """helper.cmd 내용을 만든다. 창 없이 켜서 PowerShell을 닫아도 꺼지지 않는다."""
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    main = root / "main.py"
    return (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'cd /d "{root}"\r\n'
        f'start "" "{pythonw}" "{main}"\r\n'
    )


def write_windows_helper(root: Path, local_app_data: Path) -> Path:
    """helper.cmd를 만들고 그 경로를 반환한다."""
    folder = local_app_data / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{HELPER_NAME}.cmd"
    path.write_bytes(windows_helper_text(root).encode("utf-8"))
    return path


def merged_path_value(current: str, folder: str) -> str | None:
    """PATH 값에 폴더를 더한 값을 반환한다. 이미 있으면 None을 반환한다."""
    entries = [entry for entry in current.split(";") if entry]
    if folder.lower() in (entry.rstrip("\\").lower() for entry in entries):
        return None
    return ";".join([*entries, folder])


def add_to_windows_user_path(folder: Path) -> bool:
    """사용자 PATH에 폴더를 추가하고, 추가했는지 반환한다."""
    import winreg  # Windows에만 있는 모듈이라 이곳에서 불러온다.

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, WINDOWS_ENVIRONMENT_KEY, 0, winreg.KEY_ALL_ACCESS
    ) as key:
        try:
            current, _value_type = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        merged = merged_path_value(current, str(folder))
        if merged is None:
            return False
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, merged)

    # 새로 여는 창이 바뀐 PATH를 쓰도록 Windows에 알린다.
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        WM_SETTINGCHANGE,
        0,
        WINDOWS_ENVIRONMENT_KEY,
        SMTO_ABORTIFHUNG,
        5000,
        None,
    )
    return True


def _powershell_quote(text: object) -> str:
    """PowerShell 작은따옴표 문자열로 감싼다."""
    escaped = str(text).replace("'", "''")
    return f"'{escaped}'"


def windows_shortcut_script(root: Path) -> str:
    """바탕화면 바로가기를 만드는 PowerShell 명령을 만든다."""
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    main = root / "main.py"
    icon = root / "assets" / "icon.ico"
    arguments = f'"{main}"'
    return (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut((Join-Path $desktop '{APP_NAME}.lnk')); "
        f"$link.TargetPath = {_powershell_quote(pythonw)}; "
        f"$link.Arguments = {_powershell_quote(arguments)}; "
        f"$link.WorkingDirectory = {_powershell_quote(root)}; "
        f"$link.IconLocation = {_powershell_quote(icon)}; "
        "$link.Save()"
    )


def create_windows_shortcut(
    root: Path, run: Callable[..., object] = subprocess.run
) -> None:
    """PowerShell로 바탕화면 바로가기를 만든다. 실패하면 오류가 발생한다."""
    run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            windows_shortcut_script(root),
        ],
        check=True,
    )


def setup_mac(root: Path, home: Path) -> None:
    """Mac에 helper 명령과 바로가기를 등록한다."""
    write_mac_helper(root, home)
    ensure_zshrc_path(home)
    write_mac_shortcut(root, home / "Desktop")


def setup_windows(root: Path) -> None:
    """Windows에 helper 명령과 바로가기를 등록한다."""
    helper = write_windows_helper(root, Path(os.environ["LOCALAPPDATA"]))
    add_to_windows_user_path(helper.parent)
    create_windows_shortcut(root)


def main() -> int:
    """운영체제에 맞게 등록하고 종료 코드를 반환한다."""
    root = Path(__file__).resolve().parent.parent
    home = Path.home()
    if ensure_project_folder(home):
        print(f"Project 폴더를 만들었습니다: {home / PROJECT_FOLDER_NAME}")
    if sys.platform == "win32":
        setup_windows(root)
    else:
        setup_mac(root, home)
    print("helper 명령과 바탕화면 바로가기를 만들었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
