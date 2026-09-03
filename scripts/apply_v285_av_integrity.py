from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# recorder: normalize live timestamps, buffer each input independently, keep video bit-exact,
# but lock audio to the media clock with a lightweight AAC/aresample stage.
replace(
    "app/recorder.py",
    '    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]\n    for item in inputs:\n        cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]\n',
    '    cmd = [\n        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",\n        "-fflags", "+genpts+discardcorrupt",\n        "-dts_delta_threshold", "1",\n    ]\n    for item in inputs:\n        cmd += [\n            "-thread_queue_size", "8192",\n            "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",\n        ]\n',
)
replace(
    "app/recorder.py",
    '    cmd += [\n        "-c", "copy",\n        "-max_interleave_delta", "10000000",\n        "-f", "segment",\n',
    '    cmd += [\n        "-c:v", "copy",\n        "-copytb", "1",\n        "-c:a", "aac",\n        "-b:a", "192k",\n        "-ar", "48000",\n        "-af", "aresample=async=1:min_hard_comp=0.100",\n        "-max_interleave_delta", "1000000",\n        "-avoid_negative_ts", "make_zero",\n        "-f", "segment",\n',
)
replace(
    "app/recorder.py",
    '        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),\n        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy", "-movflags", "+faststart", str(output),\n',
    '        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",\n        "-fflags", "+genpts+discardcorrupt", "-i", str(source),\n        "-map", "0", "-dn", "-ignore_unknown", "-c", "copy",\n        "-max_interleave_delta", "1000000", "-avoid_negative_ts", "make_zero",\n        "-movflags", "+faststart", str(output),\n',
)

# integrity: reject obvious A/V drift and large video timestamp holes before cloud upload.
replace(
    "app/utils.py",
    'def _probe_duration(path: Path) -> float | None:\n',
    '''def _finite_float(value) -> float | None:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return None\n    return parsed if math.isfinite(parsed) else None\n\n\ndef _rate_seconds(value) -> float | None:\n    text = str(value or "").strip()\n    if not text or text in {"0/0", "N/A"}:\n        return None\n    try:\n        if "/" in text:\n            numerator, denominator = text.split("/", 1)\n            rate = float(numerator) / float(denominator)\n        else:\n            rate = float(text)\n        return 1.0 / rate if math.isfinite(rate) and rate > 0 else None\n    except (TypeError, ValueError, ZeroDivisionError):\n        return None\n\n\ndef _stream_timing_error(streams: list[dict]) -> str:\n    video = next((row for row in streams if row.get("codec_type") == "video"), None)\n    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)\n    if not video or not audio:\n        return ""\n    video_start = _finite_float(video.get("start_time"))\n    audio_start = _finite_float(audio.get("start_time"))\n    if video_start is not None and audio_start is not None:\n        offset = abs(video_start - audio_start)\n        if offset > 1.5:\n            return f"A/V fuori sync all'avvio: {offset:.2f}s"\n    video_duration = _finite_float(video.get("duration"))\n    audio_duration = _finite_float(audio.get("duration"))\n    if video_duration is not None and audio_duration is not None:\n        delta = abs(video_duration - audio_duration)\n        tolerance = max(2.0, min(video_duration, audio_duration) * 0.001)\n        if delta > tolerance:\n            return f"A/V fuori sync a fine file: differenza {delta:.2f}s"\n    return ""\n\n\ndef _video_gap_error(path: Path, streams: list[dict]) -> str:\n    video = next((row for row in streams if row.get("codec_type") == "video"), None)\n    expected = _rate_seconds(video.get("avg_frame_rate")) if video else None\n    threshold = max(1.5, (expected or (1 / 30)) * 12)\n    try:\n        probe = subprocess.run(\n            [\n                "ffprobe", "-v", "error", "-select_streams", "v:0",\n                "-show_entries", "packet=pts_time,dts_time", "-of", "json", str(path),\n            ],\n            capture_output=True, text=True, timeout=180, check=False,\n        )\n        if probe.returncode != 0:\n            return (probe.stderr or "Analisi timestamp video fallita")[-1200:]\n        payload = json.loads(probe.stdout or "{}")\n        previous = None\n        maximum = 0.0\n        for packet in payload.get("packets") or []:\n            current = _finite_float(packet.get("dts_time"))\n            if current is None:\n                current = _finite_float(packet.get("pts_time"))\n            if current is None:\n                continue\n            if previous is not None and current > previous:\n                maximum = max(maximum, current - previous)\n            previous = current\n        if maximum > threshold:\n            return f"Gap video rilevato: {maximum:.2f}s senza frame continui"\n        return ""\n    except subprocess.TimeoutExpired:\n        return "Analisi timestamp video scaduta"\n    except Exception as exc:\n        return f"Analisi timestamp video fallita: {exc}"[-1200:]\n\n\ndef _probe_duration(path: Path) -> float | None:\n''',
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

# Existing command tests must assert the new hybrid video-copy/audio-normalized pipeline.
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n',)
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n',)
replace("tests/test_recorder.py", '    assert "-c copy" in joined\n', '    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n',)
replace(
    "tests/test_recorder.py",
    '    assert "frag_keyframe" in joined\n',
    '    assert "frag_keyframe" in joined\n    assert "-fflags +genpts+discardcorrupt" in joined\n    assert "-dts_delta_threshold 1" in joined\n    assert "-thread_queue_size 8192" in joined\n    assert "aresample=async=1:min_hard_comp=0.100" in joined\n    assert "-max_interleave_delta 1000000" in joined\n',
)

# Release metadata / PWA cache.
for path in ["VERSION"]:
    (ROOT / path).write_text("2.8.5\n", encoding="utf-8")
for path in ["README.md", "START_HERE.md", "app/main.py", "tests/test_version_consistency.py", "app/static/sw.js"]:
    target = ROOT / path
    text = target.read_text(encoding="utf-8").replace("2.8.4", "2.8.5")
    target.write_text(text, encoding="utf-8")

changelog = ROOT / "CHANGELOG.md"
text = changelog.read_text(encoding="utf-8")
entry = """## 2.8.5 — 2026-09-03\n- Registratore: normalizzazione PTS/DTS e correzione delle discontinuità live sopra 1 secondo.\n- Video ancora in stream-copy; audio normalizzato in AAC 48 kHz con resampling asincrono per evitare drift A/V.\n- Code FFmpeg dedicate per input e interleave ridotto a 1 secondo.\n- Integrity Guard: rifiuta upload con forte offset A/V o gap video temporali.\n\n"""
if entry not in text:
    text = text.replace("# Changelog\n\n", "# Changelog\n\n" + entry, 1)
    changelog.write_text(text, encoding="utf-8")

# Dedicated regression test for the new recorder contract.
(ROOT / "tests/test_v285_av_integrity.py").write_text('''from pathlib import Path\n\nfrom app.recorder import build_ffmpeg_command\nfrom app.source_providers import ResolvedInput\n\n\ndef test_live_recorder_normalizes_timestamps_without_reencoding_video():\n    cmd = build_ffmpeg_command(\n        [ResolvedInput("https://example.test/live.m3u8", {}, "media")],\n        Path("out_%03d.mp4"),\n        segment_minutes=10,\n        container_format="mp4",\n    )\n    joined = " ".join(cmd)\n    assert "-fflags +genpts+discardcorrupt" in joined\n    assert "-dts_delta_threshold 1" in joined\n    assert "-thread_queue_size 8192" in joined\n    assert "-c:v copy" in joined\n    assert "-c:a aac" in joined\n    assert "-ar 48000" in joined\n    assert "aresample=async=1:min_hard_comp=0.100" in joined\n    assert "-max_interleave_delta 1000000" in joined\n    assert "-avoid_negative_ts make_zero" in joined\n    assert "-c:v libx264" not in joined\n''', encoding="utf-8")
