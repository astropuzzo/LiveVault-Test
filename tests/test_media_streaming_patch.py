from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parents[1]
PATCH_PATH = ROOT / 'control-panel' / 'media_streaming_patch.py'
spec = importlib.util.spec_from_file_location('media_streaming_patch_test', PATCH_PATH)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


def test_patch_uses_mpegts_hls_and_browser_safe_mime():
    text = PATCH_PATH.read_text(encoding='utf-8')
    assert "'-hls_segment_type', 'mpegts'" in text
    assert "'seg-%05d.ts'" in text
    assert "'video/mp2t'" in text
    assert "'-hls_init_time', '1'" in text
    assert "selected_audio_stream" in text
    assert "audio_stream=audio_stream" in text


def test_apply_replaces_only_hls_entrypoints():
    stream = SimpleNamespace(_openastro_media_patch_v15=False)
    # apply() installs closures without executing any ffmpeg work.
    patch.apply(stream)
    assert callable(stream.start_hls)
    assert callable(stream.segment_info)
    assert stream._openastro_media_patch_v15 is True
