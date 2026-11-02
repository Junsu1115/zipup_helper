"""설치 스크립트 테스트. 임시 홈 폴더만 쓰고, 실제 사용자 설정은 건드리지 않는다."""

import os
import stat
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock

import pytest

from scripts.setup_launcher import (
    ZSHRC_MARKER,
    create_windows_shortcut,
    ensure_project_folder,
    ensure_zshrc_path,
    merged_path_value,
    windows_helper_text,
    windows_shortcut_script,
    write_mac_helper,
    write_mac_shortcut,
    write_windows_helper,
)

ROOT = Path(__file__).resolve().parent.parent


def test_project_folder_is_created_once_and_kept(tmp_path: Path) -> None:
    """Project 폴더를 만들고, 이미 있으면 내용을 건드리지 않는지 확인한다."""
    assert ensure_project_folder(tmp_path) is True
    (tmp_path / "Project" / "기존").mkdir()

    assert ensure_project_folder(tmp_path) is False
    assert (tmp_path / "Project" / "기존").is_dir()


@pytest.mark.skipif(os.name == "nt", reason="Mac 실행 권한은 Windows에서 확인할 수 없다")
def test_mac_helper_is_executable_and_runs_project_python(tmp_path: Path) -> None:
    """helper 명령이 실행 가능하고, 터미널과 분리해 프로그램을 켜는지 확인한다."""
    root = tmp_path / "작업 폴더" / "zipup_helper-main"

    helper = write_mac_helper(root, tmp_path)

    assert helper == tmp_path / ".local" / "bin" / "helper"
    assert helper.stat().st_mode & stat.S_IXUSR
    text = helper.read_text(encoding="utf-8")
    assert f'nohup "{root / ".venv" / "bin" / "python"}" "{root / "main.py"}"' in text
    assert text.rstrip().endswith("&")


def test_zshrc_is_updated_once_and_keeps_contents(tmp_path: Path) -> None:
    """.zshrc의 기존 내용을 유지하고, 여러 번 실행해도 한 번만 추가하는지 확인한다."""
    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("export 기존=1", encoding="utf-8")

    assert ensure_zshrc_path(tmp_path) is True
    assert ensure_zshrc_path(tmp_path) is False

    text = zshrc.read_text(encoding="utf-8")
    assert text.startswith("export 기존=1\n")
    assert text.count(ZSHRC_MARKER) == 1
    assert 'export PATH="$HOME/.local/bin:$PATH"' in text


def test_zshrc_is_created_when_missing(tmp_path: Path) -> None:
    """.zshrc가 없으면 새로 만드는지 확인한다."""
    assert ensure_zshrc_path(tmp_path) is True
    assert ZSHRC_MARKER in (tmp_path / ".zshrc").read_text(encoding="utf-8")


def test_mac_shortcut_has_app_structure_and_icon(tmp_path: Path) -> None:
    """바탕화면 앱 묶음에 실행 파일, 설정, 아이콘이 있는지 확인한다."""
    root = tmp_path / "program"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "icon.icns").write_bytes(b"icon")

    app = write_mac_shortcut(root, tmp_path / "Desktop")

    assert app == tmp_path / "Desktop" / "zipup_helper.app"
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    assert "<key>CFBundleExecutable</key>" in plist
    assert "<key>CFBundleIconFile</key>" in plist
    assert (app / "Contents" / "Resources" / "icon.icns").read_bytes() == b"icon"
    executable = app / "Contents" / "MacOS" / "zipup_helper"
    assert str(root / "main.py") in executable.read_text(encoding="utf-8")


def test_mac_shortcut_can_be_recreated(tmp_path: Path) -> None:
    """설치를 다시 실행해도 바로가기를 덮어쓸 수 있는지 확인한다."""
    root = tmp_path / "program"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "icon.icns").write_bytes(b"icon")

    write_mac_shortcut(root, tmp_path / "Desktop")
    write_mac_shortcut(root, tmp_path / "Desktop")


def test_windows_helper_starts_program_without_console() -> None:
    """helper.cmd가 pythonw로 프로그램을 따로 켜는지 확인한다."""
    root = PureWindowsPath(r"C:\Users\홍 길동\Documents\zipup_helper-main")

    text = windows_helper_text(root)

    assert text.startswith("@echo off\r\nchcp 65001")
    assert (
        rf'start "" "{root}\.venv\Scripts\pythonw.exe" "{root}\main.py"' in text
    )
    assert "\n" not in text.replace("\r\n", "")


def test_windows_helper_file_is_written_under_local_app_data(tmp_path: Path) -> None:
    """helper.cmd가 정해진 폴더에 만들어지는지 확인한다."""
    path = write_windows_helper(tmp_path / "program", tmp_path / "local")

    assert path == tmp_path / "local" / "zipup_helper" / "helper.cmd"
    assert path.read_bytes().startswith(b"@echo off\r\n")


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        ("", r"C:\app"),
        (r"C:\a;C:\b", r"C:\a;C:\b;C:\app"),
        (r"C:\a;", r"C:\a;C:\app"),
        (r"C:\a;C:\APP", None),
        (r"C:\app\;C:\a", None),
    ],
)
def test_merged_path_adds_folder_once(current: str, expected: str | None) -> None:
    """PATH에 폴더를 한 번만 더하고, 대소문자와 끝의 \\ 차이를 무시하는지 확인한다."""
    assert merged_path_value(current, r"C:\app") == expected


def test_windows_shortcut_script_sets_target_and_icon() -> None:
    """바로가기가 pythonw, main.py, 아이콘을 가리키고, 작은따옴표를 처리하는지 확인한다."""
    root = PureWindowsPath(r"C:\Users\O'Neil\zipup")

    script = windows_shortcut_script(root)

    assert "[Environment]::GetFolderPath('Desktop')" in script
    assert "zipup_helper.lnk" in script
    assert r"'C:\Users\O''Neil\zipup\.venv\Scripts\pythonw.exe'" in script
    assert r"""'"C:\Users\O''Neil\zipup\main.py"'""" in script
    assert r"'C:\Users\O''Neil\zipup\assets\icon.ico'" in script


def test_windows_shortcut_runs_powershell_without_profile() -> None:
    """프로필과 실행 정책의 영향을 받지 않게 PowerShell을 실행하는지 확인한다."""
    run = MagicMock()

    create_windows_shortcut(PureWindowsPath(r"C:\zipup"), run)

    command = run.call_args.args[0]
    assert command[:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]
    assert run.call_args.kwargs["check"] is True


def test_install_scripts_exist_with_correct_line_endings() -> None:
    """설치 파일의 저장소 설정과 실행 흐름을 확인한다."""
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.bat text eol=crlf" in attributes
    assert "*.command text eol=lf" in attributes

    raw_bat = (ROOT / "install_windows.bat").read_bytes()
    assert b"\n" not in raw_bat.replace(b"\r\n", b"")
    assert b"\r" not in (ROOT / "install_mac.command").read_bytes()

    bat = raw_bat.decode("utf-8")
    assert "chcp 65001" in bat
    assert r"scripts\setup_launcher.py" in bat

    command = (ROOT / "install_mac.command").read_text(encoding="utf-8")
    assert command.startswith("#!/bin/bash")
    assert "scripts/setup_launcher.py" in command


def test_icon_files_exist() -> None:
    """바로가기에 쓰는 아이콘 파일이 있는지 확인한다."""
    assert (ROOT / "assets" / "icon.ico").stat().st_size > 0
    assert (ROOT / "assets" / "icon.icns").stat().st_size > 0
