"""core.external_apps 테스트. 명령만 만들 뿐 프로그램을 실제로 실행하지 않는다."""

from pathlib import Path

from core.external_apps import (
    EXTERNAL_APPS,
    ExternalApp,
    build_default_open_command,
    build_open_command,
)


def test_photoshop_is_registered_with_bundle_id() -> None:
    """포토샵이 버전과 상관없는 고유 식별자로 등록되어 있는지 확인한다."""
    assert ExternalApp("Adobe Photoshop", "com.adobe.Photoshop") in EXTERNAL_APPS


def test_default_open_command_has_no_app_and_keeps_path_whole() -> None:
    """기본 프로그램 명령은 프로그램을 지정하지 않고, 경로를 인자 하나로 전달하는지 확인한다."""
    assert build_default_open_command(Path("/작업 폴더/사진 1.png")) == [
        "/usr/bin/open",
        "/작업 폴더/사진 1.png",
    ]


def test_open_command_keeps_path_with_spaces_as_one_argument() -> None:
    """공백과 한글이 있는 경로도 인자 하나로 전달되는지 확인한다."""
    app = ExternalApp("테스트앱", "com.example.app")
    file = Path("/작업 폴더/사진 1.png")

    assert build_open_command(app, file) == [
        "/usr/bin/open",
        "-b",
        "com.example.app",
        "/작업 폴더/사진 1.png",
    ]
