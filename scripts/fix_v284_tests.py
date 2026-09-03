from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Pulse regression now validates the exact physical recording segments used for
# hover preview + remote click, while keeping all CSP/geometry assertions.
replace_once(
    "tests/test_live_pulse_ui.py",
    "    assert 'pulseRecordingIntervals(session)' in pulse\n",
    "    assert 'pulseRecordingFiles(session)' in pulse\n",
)
replace_once(
    "tests/test_live_pulse_ui.py",
    "    assert '<rect class=\"cr-pulse-rec-span\"' in pulse\n",
    "    assert \"cr-pulse-rec-span ${remoteUrl ? 'remote' : ''}\" in pulse\n    assert 'cr-pulse-rec-media' in pulse\n    assert 'data-preview-url' in pulse\n    assert 'target=\"_blank\"' in pulse\n",
)

# Storyboard contract is now nine input-side seeks assembled as a 3x3 sheet.
replace_once(
    "tests/test_media_integrity.py",
    "def test_thumbnail_storyboard_uses_four_fast_seeks(tmp_path: Path, monkeypatch):",
    "def test_thumbnail_storyboard_uses_nine_fast_seeks(tmp_path: Path, monkeypatch):",
)
replace_once("tests/test_media_integrity.py", '    assert command.count("-ss") == 4\n', '    assert command.count("-ss") == 9\n')
replace_once("tests/test_media_integrity.py", '    assert command.count("-i") == 4\n', '    assert command.count("-i") == 9\n')
replace_once(
    "tests/test_media_integrity.py",
    '    assert "hstack=inputs=2" in command[command.index("-filter_complex") + 1]\n    assert "vstack=inputs=2" in command[command.index("-filter_complex") + 1]\n',
    '    assert "hstack=inputs=3" in command[command.index("-filter_complex") + 1]\n    assert "vstack=inputs=3" in command[command.index("-filter_complex") + 1]\n',
)
replace_once("tests/test_media_integrity.py", '    assert dimensions.stdout.strip() == "640x360"\n', '    assert dimensions.stdout.strip() == "960x540"\n')

# Release consistency + changelog format.
replace_once("tests/test_version_consistency.py", '    assert version == "2.8.3"\n', '    assert version == "2.8.4"\n')
replace_once("CHANGELOG.md", "## 2.8.4\n", "## 2.8.4 — 2026-09-03\n")

print("v2.8.4 stale regression expectations updated")
