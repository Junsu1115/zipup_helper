"""core.external_apps 테스트. 명령만 만들 뿐 프로그램을 실제로 실행하지 않는다."""

from pathlib import Path

import pytest

from core.external_apps import (
    EXTERNAL_APPS,
    AppNotFoundError,
    ExternalApp,
    MacAppLauncher,
    WindowsAppLauncher,
    get_app_launcher,
)

TEST_APP = ExternalApp("테스트앱", "com.example.app", "App.exe")
TEST_FILE = Path("작업 폴더") / "사진 1.png"


def test_photoshop_is_registered_with_both_identifiers() -> None:
    """포토샵이 버전과 상관없는 운영체제별 식별 정보로 등록되어 있는지 확인한다."""
    assert (
        ExternalApp("Adobe Photoshop", "com.adobe.Photoshop", "Photoshop.exe")
        in EXTERNAL_APPS
    )


def test_mac_command_uses_bundle_id_and_keeps_path_whole() -> None:
    """macOS 명령이 번들 식별자를 쓰고, 경로를 인자 하나로 전달하는지 확인한다."""
    launcher = MacAppLauncher()

    assert launcher.build_command(TEST_APP, TEST_FILE) == [
        "/usr/bin/open",
        "-b",
        "com.example.app",
        str(TEST_FILE),
    ]
    assert launcher.opens_detached is False


def test_windows_command_uses_registered_path_and_keeps_path_whole() -> None:
    """Windows 명령이 등록된 설치 경로로 실행하고, 종료를 기다리지 않는지 확인한다."""
    executable = r"C:\Program Files\Adobe\Photoshop.exe"
    requested: list[str] = []

    def read_app_path(name: str) -> str:
        """요청받은 실행 파일 이름을 기록하고 설치 경로를 반환한다."""
        requested.append(name)
        return executable

    launcher = WindowsAppLauncher(read_app_path)

    assert launcher.build_command(TEST_APP, TEST_FILE) == [executable, str(TEST_FILE)]
    assert requested == ["App.exe"]
    assert launcher.opens_detached is True


def test_windows_command_raises_when_app_is_not_registered() -> None:
    """설치 경로를 찾지 못하면 오류가 발생하는지 확인한다."""
    launcher = WindowsAppLauncher(lambda _name: None)

    with pytest.raises(AppNotFoundError):
        launcher.build_command(TEST_APP, TEST_FILE)


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", WindowsAppLauncher), ("darwin", MacAppLauncher)],
)
def test_get_app_launcher_matches_platform(
    platform: str, expected: type
) -> None:
    """운영체제에 맞는 실행 방식을 고르는지 확인한다."""
    assert isinstance(get_app_launcher(platform), expected)
