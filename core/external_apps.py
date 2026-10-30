"""사진을 열 수 있는 외부 프로그램 정보와 실행 명령."""

from dataclasses import dataclass
from pathlib import Path

OPEN_COMMAND = "/usr/bin/open"


@dataclass(frozen=True)
class ExternalApp:
    """외부 프로그램. 버전이 달라도 찾을 수 있도록 고유 식별자로 구분한다."""

    name: str
    bundle_id: str


# 프로그램을 추가하려면 이 목록에 넣는다. 오른쪽 칸에 버튼이 함께 생긴다.
EXTERNAL_APPS = (
    ExternalApp("Adobe Photoshop", "com.adobe.Photoshop"),
)


def build_open_command(app: ExternalApp, file: Path) -> list[str]:
    """파일을 해당 프로그램으로 여는 macOS 명령을 만든다."""
    return [OPEN_COMMAND, "-b", app.bundle_id, str(file)]


def build_default_open_command(file: Path) -> list[str]:
    """파일을 macOS 기본 프로그램으로 여는 명령을 만든다. Finder에서 더블클릭한 것과 같다."""
    return [OPEN_COMMAND, str(file)]
