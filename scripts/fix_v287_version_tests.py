from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for relative in ("tests/test_v284_pulse_media.py", "tests/test_v286_daily_cloud.py"):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count("2.8.6")
    if count != 3:
        raise RuntimeError(f"{relative}: expected 3 legacy version assertions, found {count}")
    path.write_text(text.replace("2.8.6", "2.8.7"), encoding="utf-8")
print("legacy version assertions updated")
