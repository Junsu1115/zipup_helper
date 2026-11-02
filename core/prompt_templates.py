"""클립보드에 복사할 프롬프트 템플릿을 읽는다."""

from pathlib import Path

IMAGE_ANALYSIS_TEMPLATE = Path(__file__).parent / "templates" / "image_analysis.txt"


def load_image_analysis_template(path: Path = IMAGE_ANALYSIS_TEMPLATE) -> str:
    """이미지 분석 템플릿을 읽어 반환한다. 읽지 못하면 OSError가 발생한다."""
    return path.read_text(encoding="utf-8").rstrip("\n")
