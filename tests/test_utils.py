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


def test_hls_audio_rendition_without_codec_metadata_is_kept():
    assert classify_format(
        "none",
        None,
        format_id="audio_aac_128-Audio_200_1_5",
        format_label="audio only (high)",
    ) == "audio"
