from pathlib import Path

updates = {
    "tests/test_v284_pulse_media.py": [
        ('assert workers.count("-sheet-v2.jpg") == 2', 'assert workers.count("-sheet-v2.jpg") == 3'),
    ],
    "tests/test_version_consistency.py": [
        ('assert version == "2.8.7"', 'assert version == "2.8.8"'),
    ],
}

for filename, replacements in updates.items():
    path = Path(filename)
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"missing test anchor in {filename}: {old}")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")

print("v2.8.8 regression expectations updated")
