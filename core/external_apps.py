"""사진을 열 수 있는 외부 프로그램 정보와 운영체제별 실행 방식."""

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAC_OPEN_COMMAND = "/usr/bin/open"
WINDOWS_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


class AppNotFoundError(Exception):
    """이 컴퓨터에서 프로그램을 찾지 못했을 때 발생하는 오류."""


@dataclass(frozen=True)
class ExternalApp:
    """외부 프로그램. 버전이 달라도 찾을 수 있도록 운영체제별 식별 정보로 구분한다."""

    name: str
    mac_bundle_id: str
    windows_executable: str


# 프로그램을 추가하려면 이 목록에 넣는다. 오른쪽 칸에 버튼이 함께 생긴다.
EXTERNAL_APPS = (
    ExternalApp("Adobe Photoshop", "com.adobe.Photoshop", "Photoshop.exe"),
)


class AppLauncher(ABC):
    """운영체제별로 프로그램을 실행하는 방식(전략 패턴)."""

    # True면 실행 후 종료를 기다리지 않는다. 프로그램이 닫힐 때까지 명령이 끝나지 않는 경우다.
    opens_detached = False

    @abstractmethod
    def build_command(self, app: ExternalApp, file: Path) -> list[str]:
        """파일을 해당 프로그램으로 여는 명령을 만든다. 없으면 AppNotFoundError를 발생시킨다."""


class MacAppLauncher(AppLauncher):
    """macOS의 open 명령으로 실행한다. 프로그램이 없으면 명령이 실패로 끝난다."""

    def build_command(self, app: ExternalApp, file: Path) -> list[str]:
        """번들 식별자로 프로그램을 지정하는 명령을 만든다."""
        return [MAC_OPEN_COMMAND, "-b", app.mac_bundle_id, str(file)]


def _read_windows_app_path(executable: str) -> str | None:
    """Windows 레지스트리에서 프로그램의 설치 경로를 읽는다. 없으면 None을 반환한다."""
    import winreg  # Windows에만 있는 모듈이라 이곳에서 불러온다.

    key_path = rf"{WINDOWS_APP_PATHS_KEY}\{executable}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            return winreg.QueryValue(hive, key_path) or None
        except OSError:
            continue
    return None


class WindowsAppLauncher(AppLauncher):
    """레지스트리에 등록된 설치 경로로 프로그램을 직접 실행한다."""

    opens_detached = True

    def __init__(
        self, read_app_path: Callable[[str], str | None] = _read_windows_app_path
    ) -> None:
        """설치 경로를 읽는 함수를 기억한다."""
        self._read_app_path = read_app_path

    def build_command(self, app: ExternalApp, file: Path) -> list[str]:
        """설치 경로로 실행하는 명령을 만든다."""
        executable_path = self._read_app_path(app.windows_executable)
        if executable_path is None:
            raise AppNotFoundError(app.name)
        return [executable_path, str(file)]


def get_app_launcher(platform: str = sys.platform) -> AppLauncher:
    """운영체제에 맞는 실행 방식을 반환한다."""
    if platform == "win32":
        return WindowsAppLauncher()
    return MacAppLauncher()
