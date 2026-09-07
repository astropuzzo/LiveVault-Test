from pathlib import Path

ROOT = Path(__file__).parents[1]
APP = (ROOT / "control-panel/static/app.js").read_text(encoding="utf-8")
CSS = (ROOT / "control-panel/static/app.css").read_text(encoding="utf-8")
INDEX = (ROOT / "control-panel/static/index.html").read_text(encoding="utf-8")
PATCH = (ROOT / "control-panel/media_streaming_patch.py").read_text(encoding="utf-8")
SW = (ROOT / "control-panel/static/sw.js").read_text(encoding="utf-8")


def test_hls_player_has_one_full_duration_timeline_not_native_controls():
    assert "FILM COMPLETO" not in INDEX
    assert 'mediaHlsPlayerMarkup()' in APP
    markup = APP.split('function mediaHlsPlayerMarkup(){', 1)[1].split('\n}', 1)[0]
    assert '<video playsinline preload="metadata"></video>' in markup
    assert '<video controls' not in markup
    assert markup.count('id="mediaPlayerSeekInput"') == 1
    assert 'aria-label="Posizione nel film"' in markup


def test_seek_reuses_player_and_uses_local_buffer_when_available():
    assert 'const localSeek=position=>' in APP
    assert 'media.seekable' in APP
    assert 'if(localSeek(position))return;' in APP
    assert 'const startHlsAt=async(position,audio=selectedAudio)' in APP
    restart = APP.split('const restartAt=async position=>', 1)[1].split(';\n', 1)[0]
    assert 'openMediaPlayer(' not in restart
    assert 'startHlsAt(position,selectedAudio)' in restart


def test_seek_startup_bursts_then_returns_to_realtime_rate():
    assert "'-readrate', '1', '-readrate_initial_burst', '12'" in PATCH
    assert "'-re'" not in PATCH


def test_custom_transport_is_mobile_compact_and_cache_bumped():
    assert '.media-video-controls{' in CSS
    assert '.media-player-clock{' in CSS
    assert '.media-transport-button{' in CSS
    assert '/app.js?v=18.0-player' in INDEX
    assert '/app.css?v=18.0-player' in INDEX
    assert 'openastro-control-v18-player' in SW
