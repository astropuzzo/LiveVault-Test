from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"anchor missing in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Timestamp discontinuities in otherwise decodable HLS media are quality warnings,
# not integrity failures. Dropping an entire fragment because of a sub-second gap
# loses far more footage than the gap itself.
replace_once(
    "app/utils.py",
    '''class IntegrityResult:\n    ok: bool\n    duration: float | None\n    error: str = ""\n    streams: list[dict] | None = None\n''',
    '''class IntegrityResult:\n    ok: bool\n    duration: float | None\n    error: str = ""\n    streams: list[dict] | None = None\n    warning: str = ""\n''',
)
replace_once(
    "app/utils.py",
    '''        gap_error = _video_gap_error(path, quick.streams or [])\n        if gap_error:\n            return IntegrityResult(False, quick.duration, gap_error, quick.streams)\n        return quick\n''',
    '''        gap_warning = _video_gap_error(path, quick.streams or [])\n        if gap_warning:\n            quick.warning = gap_warning\n        return quick\n''',
)

# Old builds may already have marked usable fragments as failed only because of
# the aggressive video-gap detector. Keep those files in the stitch set so they
# can be recovered by the new non-fatal validator instead of being deleted.
replace_once(
    "app/workers.py",
    'RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor")',
    'RETRYABLE_MEDIA_ERRORS = ("scadut", "timeout", "timed out", "tempor", "gap video")',
)
replace_once(
    "app/workers.py",
    '''def stitch_gap_open(last_at: datetime, now: datetime, gap_seconds: int = SESSION_STITCH_GAP_SECONDS) -> bool:\n    if last_at.tzinfo is None:\n        last_at = last_at.replace(tzinfo=timezone.utc)\n    if now.tzinfo is None:\n        now = now.replace(tzinfo=timezone.utc)\n    delta = (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds()\n    return 0 <= delta <= max(0, int(gap_seconds))\n''',
    '''def stitch_gap_open(last_at: datetime, now: datetime, gap_seconds: int = SESSION_STITCH_GAP_SECONDS) -> bool:\n    if last_at.tzinfo is None:\n        last_at = last_at.replace(tzinfo=timezone.utc)\n    if now.tzinfo is None:\n        now = now.replace(tzinfo=timezone.utc)\n    delta = (now.astimezone(timezone.utc) - last_at.astimezone(timezone.utc)).total_seconds()\n    return 0 <= delta <= max(0, int(gap_seconds))\n\n\ndef fragment_usable_for_stitch(fragment: RecordingFragment) -> bool:\n    path = Path(fragment.local_path)\n    if not path.is_file():\n        return False\n    if fragment.integrity_status == "passed":\n        return True\n    # Rescue fragments indexed by pre-hotfix builds where the only failure was\n    # a timestamp discontinuity. The final combined media is verified again.\n    error = str(fragment.integrity_error or "").lower()\n    return fragment.integrity_status == "failed" and error.startswith("gap video rilevato:")\n''',
)
replace_once(
    "app/workers.py",
    '''        good = [item for item in fragments if item.integrity_status == "passed" and Path(item.local_path).is_file()]\n''',
    '''        good = [item for item in fragments if fragment_usable_for_stitch(item)]\n''',
)

# Regression coverage: a readable file with a reported timestamp gap remains OK,
# and already-indexed legacy gap-only fragments are recovered into stitching.
media_tests = Path("tests/test_media_integrity.py")
text = media_tests.read_text(encoding="utf-8")
if "test_video_timestamp_gap_is_warning_not_integrity_failure" not in text:
    text += '''\n\ndef test_video_timestamp_gap_is_warning_not_integrity_failure(tmp_path: Path, monkeypatch):\n    media = tmp_path / "gap.mp4"\n    media.write_bytes(b"placeholder")\n    quick = __import__("app.utils", fromlist=["IntegrityResult"]).IntegrityResult(\n        True, 60.0, "", [{"codec_type": "video", "avg_frame_rate": "30/1"}, {"codec_type": "audio"}]\n    )\n\n    monkeypatch.setattr("app.utils.probe_media", lambda *_args, **_kwargs: quick)\n    monkeypatch.setattr(\n        "app.utils.subprocess.run",\n        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),\n    )\n    monkeypatch.setattr("app.utils._video_gap_error", lambda *_args, **_kwargs: "Gap video rilevato: 0.94s senza frame continui")\n\n    result = verify_media(media, "packet")\n    assert result.ok\n    assert result.warning == "Gap video rilevato: 0.94s senza frame continui"\n'''
    media_tests.write_text(text, encoding="utf-8")

worker_tests = Path("tests/test_worker_monitoring.py")
text = worker_tests.read_text(encoding="utf-8")
if "test_legacy_gap_only_fragment_remains_stitchable" not in text:
    if "from app.workers import" in text:
        # Keep existing imports untouched; use a local import in the test.
        pass
    text += '''\n\ndef test_legacy_gap_only_fragment_remains_stitchable(tmp_path):\n    from types import SimpleNamespace\n    from app.workers import fragment_usable_for_stitch\n\n    path = tmp_path / "part.mp4"\n    path.write_bytes(b"media")\n    fragment = SimpleNamespace(\n        local_path=str(path),\n        integrity_status="failed",\n        integrity_error="Gap video rilevato: 0.94s senza frame continui",\n    )\n    assert fragment_usable_for_stitch(fragment) is True\n\n    fragment.integrity_error = "Packet scan failed"\n    assert fragment_usable_for_stitch(fragment) is False\n'''
    worker_tests.write_text(text, encoding="utf-8")

print("video gap preservation hotfix applied")
