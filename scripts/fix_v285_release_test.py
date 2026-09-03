from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests/test_v284_pulse_media.py"
text = path.read_text(encoding="utf-8")
old = 'assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.4"'
new = 'assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() == "2.8.5"'
if old not in text:
    raise SystemExit("v2.8.4 release assertion not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
