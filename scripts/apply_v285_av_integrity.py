from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Recorder: normalize every live input independently. Preserve the video bitstream,
# while rebuilding the audio clock so HLS discontinuities cannot accumulate as A/V drift.
replace(
    "app/recorder.py",
    '    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]\n    for item in inputs:\n        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]\n',
    '    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]\n    for item in inputs:\n        cmd += [\n            "-fflags", "+genpts+discardcorrupt",\n            "-dts_delta_threshold", "1",\n            "-thread_queue_size", "8192",\n            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",\n        ]\n',
)
replace(
    "app/recorder.py",
    '    cmd += [\n        "-c", "copy",\n        "-max_interleave_delta", "10000000",\n        "-f", "segment",\n',
    '    cmd += [\n        "-c:v", "copy",\n        "-copytb", "1",\n        "-c:a", "aac",\n        "-b:a", "192k",\n        "-ar", "48000",\n        "-af", "aresample=async=1000:first_pts=0:min_hard_comp=0.100",\n        "-max_interleave_delta", "1000000",\n        "-avoid_negative_ts", "make_zero",\n        "-f", "segment",\n',
)
replace(
    "app/recorder.py",
    '        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),\n        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy", "-movflags", "+faststart", str(output),\n',
    '        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",\n        "-fflags", "+genpts+discardcorrupt", "-i", str(source),\n        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy",\n        "-max_interleave_delta", "1000000", "-avoid_negative_ts", "make_zero",\n        "-movflags", "+faststart", str(output),\n',
)

# Integrity Guard: reject obvious A/V drift and long timestamp holes before upload.
replace(
    "app/utils.py",
    'def _probe_duration(path: Path) -> float | None:\n',
    '''def _finite_float(value) -> float | None:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return None\n    return parsed if math.isfinite(parsed) else None\n\n\ndef _rate_seconds(value) -> float | None:\n    text = str(value or "").strip()\n    if not text or text in {"0/0", "N/A"}:\n        return None\n    try:\n        if "/" in text:\n            numerator, denominator = text.split("/", 1)\n            rate = float(numerator) / float(denominator)\n        else:\n            rate = float(text)\n        return 1.0 / rate if math.isfinite(rate) and rate > 0 else None\n    except (TypeError, ValueError, ZeroDivisionError):\n        return None\n\n\ndef _stream_timing_error(streams: list[dict]) -> str:\n    video = next((row for row in streams if row.get("codec_type") == "video"), None)\n    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)\n    if not video or not audio:\n        return ""\n    video_start = _finite_float(video.get("start_time"))\n    audio_start = _finite_float(audio.get("start_time"))\n    if video_start is not None and audio_start is not None:\n        offset = abs(video_start - audio_start)\n        if offset > 1.5:\n            return f"A/V fuori sync all'avvio: {offset:.2f}s"\n    video_duration = _finite_float(video.get("duration"))\n    audio_duration = _finite_float(audio.get("duration"))\n    if video_duration is not None and audio_duration is not None:\n        delta = abs(video_duration - audio_duration)\n        tolerance = max(2.0, min(video_duration, audio_duration) * 0.001)\n        if delta > tolerance:\n            return f"A/V fuori sync a fine file: differenza {delta:.2f}s"\n    return ""\n\n\ndef _video_gap_error(path: Path, streams: list[dict]) -> str:\n    video = next((row for row in streams if row.get("codec_type") == "video"), None)\n    expected = _rate_seconds(video.get("avg_frame_rate")) if video else None\n    threshold = max(0.75, (expected or (1 / 30)) * 12)\n    try:\n        probe = subprocess.run(\n            [\n                "ffprobe", "-v", "error", "-select_streams", "v:0",\n                "-show_entries", "packet=dts_time,pts_time", "-of", "csv=p=0", str(path),\n            ],\n            capture_output=True, text=True, timeout=180, check=False,\n        )\n        if probe.returncode != 0:\n            return (probe.stderr or "Analisi timestamp video fallita")[-1200:]\n        previous = None\n        maximum = 0.0\n        for line in (probe.stdout or "").splitlines():\n            parts = [part.strip() for part in line.split(",")]\n            current = next((_finite_float(part) for part in parts if _finite_float(part) is not None), None)\n            if current is None:\n                continue\n            if previous is not None and current > previous:\n                maximum = max(maximum, current - previous)\n            previous = current\n        if maximum > threshold:\n            return f"Gap video rilevato: {maximum:.2f}s senza frame continui"\n        return ""\n    except subprocess.TimeoutExpired:\n        return "Analisi timestamp video scaduta"\n    except Exception as exc:\n        return f"Analisi timestamp video fallita: {exc}"[-1200:]\n\n\ndef _probe_duration(path: Path) -> float | None:\n''',
)
replace(
    "app/utils.py",
    '                "format=format_name:stream=index,codec_type,codec_name",\n',
    '                "format=format_name:stream=index,codec_type,codec_name,start_time,duration,avg_frame_rate",\n',
)
replace(
    "app/utils.py",
    '        if path.suffix.lower() == ".mp4" and (duration is None or not math.isfinite(duration) or duration <= 0):\n',
    '        timing_error = _stream_timing_error(streams)\n        if timing_error:\n            return IntegrityResult(False, duration, timing_error, streams)\n        if path.suffix.lower() == ".mp4" and (duration is None or not math.isfinite(duration) or duration <= 0):\n',
)
replace(
    "app/utils.py",
    '        if p.returncode != 0:\n            return IntegrityResult(False, quick.duration, (p.stderr or "Packet scan failed")[-1600:], quick.streams)\n        return quick\n',
    '        if p.returncode != 0:\n            return IntegrityResult(False, quick.duration, (p.stderr or "Packet scan failed")[-1600:], quick.streams)\n        gap_error = _video_gap_error(path, quick.streams or [])\n        if gap_error:\n            return IntegrityResult(False, quick.duration, gap_error, quick.streams)\n        return quick\n',
)

# Existing command tests now assert the hybrid video-copy/audio-normalized pipeline.
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n')
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n')
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n')
replace(
    "tests/test_recorder.py",
    '    assert "frag_keyframe" in joined\n',
    '    assert "frag_keyframe" in joined\n    assert "-fflags +genpts+discardcorrupt" in joined\n    assert "-dts_delta_threshold 1" in joined\n    assert "-thread_queue_size 8192" in joined\n    assert "aresample=async=1000:first_pts=0:min_hard_comp=0.100" in joined\n    assert "-max_interleave_delta 1000000" in joined\n',
)

# Release metadata / PWA cache.
(ROOT / "VERSION").write_text("2.8.5\n", encoding="utf-8")
for path in ["README.md", "START_HERE.md", "app/main.py", "tests/test_version_consistency.py", "app/static/sw.js"]:
    target = ROOT / path
    text = target.read_text(encoding="utf-8").replace("2.8.4", "2.8.5")
    target.write_text(text, encoding="utf-8")
replace(
    "tests/test_v284_pulse_media.py",
    '''def test_release_is_v284():\n    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.4"\n    assert 'VERSION = "2.8.4"' in (ROOT / "app/main.py").read_text(encoding="utf-8")\n    assert "livevault-shell-v2.8.4" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")\n''',
    '''def test_release_tracks_current_version():\n    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.5"\n    assert 'VERSION = "2.8.5"' in (ROOT / "app/main.py").read_text(encoding="utf-8")\n    assert "livevault-shell-v2.8.5" in (ROOT / "app/static/sw.js").read_text(encoding="utf-8")\n''',
)

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
entry = """## 2.8.5 — 2026-09-03\n- Registratore: normalizzazione PTS/DTS su ogni input live e correzione delle discontinuità oltre 1 secondo.\n- Video ancora in stream-copy; audio rigenerato in AAC 48 kHz con clock asincrono per evitare drift A/V.\n- Code FFmpeg dedicate per input e interleave ridotto a 1 secondo.\n- Integrity Guard: blocca upload con forte offset A/V o gap video temporali.\n\n"""
if entry not in text:
    text = text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    changelog.write_text(text, encoding="utf-8")

# Dedicated regressions for the critical recorder contract.
(ROOT / "tests/test_v285_av_integrity.py").write_text('''from pathlib import Path\nfrom types import SimpleNamespace\n\nimport app.utils as utils\nfrom app.recorder import build_ffmpeg_command\nfrom app.source_providers import ResolvedInput\n\n\ndef test_live_recorder_normalizes_timestamps_without_reencoding_video():\n    cmd = build_ffmpeg_command(\n        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n    )\n    joined = " ".join(cmd)\n    assert "-fflags +genpts+discardcorrupt" in joined\n    assert "-dts_delta_threshold 1" in joined\n    assert "-thread_queue_size 8192" in joined\n    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n    assert "-ar 48000" in joined\n    assert "aresample=async=1000:first_pts=0:min_hard_comp=0.100" in joined\n    assert "-max_interleave_delta 1000000" in joined\n    assert "-avoid_negative_ts make_zero" in joined\n    assert "-c:v libx264" not in joined\n\n\ndef test_timestamp_normalization_is_applied_to_each_separate_input():\n    cmd = build_ffmpeg_command(\n        [\n            ResolvedInput("https://example.test/video.m3u8", {}, "video"),\n            ResolvedInput("https://example.test/audio.m3u8", {}, "audio"),\n        ],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n    )\n    joined = " ".join(cmd)\n    assert joined.count("-fflags +genpts+discardcorrupt") == 2\n    assert joined.count("-dts_delta_threshold 1") == 2\n    assert joined.count("-thread_queue_size 8192") == 2\n\n\ndef test_integrity_guard_rejects_large_av_drift():\n    streams = [\n        {"codec_type": "video", "start_time": "0", "duration": "100", "avg_frame_rate": "30/1"},\n        {"codec_type": "audio", "start_time": "0.02", "duration": "103.2"},\n    ]\n    assert "fuori sync" in utils._stream_timing_error(streams)\n\n\ndef test_integrity_guard_accepts_small_encoder_skew():\n    streams = [\n        {"codec_type": "video", "start_time": "0", "duration": "100", "avg_frame_rate": "30/1"},\n        {"codec_type": "audio", "start_time": "0.02", "duration": "100.04"},\n    ]\n    assert utils._stream_timing_error(streams) == ""\n\n\ndef test_integrity_guard_detects_video_timestamp_hole(monkeypatch, tmp_path):\n    fake = SimpleNamespace(returncode=0, stdout="0.000,0.000\\n0.033,0.033\\n0.066,0.066\\n1.500,1.500\\n", stderr="")\n    monkeypatch.setattr(utils.subprocess, "run", lambda *args, **kwargs: fake)\n    streams = [{"codec_type": "video", "avg_frame_rate": "30/1"}]\n    error = utils._video_gap_error(tmp_path / "clip.mp4", streams)\n    assert "Gap video rilevato" in error\n''', encoding="utf-8")
