from app.utils import safe_name
from app.source_providers import classify_format, source_url


def test_safe_name():
    assert safe_name(" hello world / x ") == "hello_world_x"
    assert safe_name("../../") == "source"


def test_source_url():
    assert source_url("chaturbate", "demo") == "https://chaturbate.com/demo/"


def test_format_classification_preserves_combined_audio_video():
    assert classify_format("h264", "aac") == "media"
    assert classify_format("h264", "none") == "video"
    assert classify_format("none", "aac") == "audio"
    assert classify_format("none", "none") == "unknown"
