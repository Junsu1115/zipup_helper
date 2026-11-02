"""core.prompt_templates 테스트."""

from pathlib import Path

import pytest

from core.prompt_templates import load_image_analysis_template


def test_template_contains_whole_instructions() -> None:
    """템플릿의 처음과 끝, 각 항목이 빠짐없이 들어 있는지 확인한다."""
    template = load_image_analysis_template()

    assert template.startswith("# Image Reproduction Analysis Instructions\n")
    assert template.endswith("along with how to correct them.")
    for heading in (
        "## 1. Camera",
        "## 2. Props by Layer (Foreground / Midground / Background)",
        "## 3. Subjects",
        "## 4. Lighting",
        "## 5. Color and Texture",
        "## 6. Output",
    ):
        assert heading in template
    assert "horizontal 0–100%" in template
    assert "focal length (mm range) — based on" in template
    assert "color & texture, --negative" in template


def test_template_keeps_line_breaks_and_indentation() -> None:
    """줄바꿈과 들여쓰기가 그대로 유지되는지 확인한다."""
    template = load_image_analysis_template()

    assert "\n\n## 1. Camera\n- Aspect ratio\n" in template
    assert "\n   Shot type, time, foreground," in template


def test_missing_template_raises_error(tmp_path: Path) -> None:
    """파일이 없으면 오류가 발생하는지 확인한다."""
    with pytest.raises(OSError):
        load_image_analysis_template(tmp_path / "없음.txt")
