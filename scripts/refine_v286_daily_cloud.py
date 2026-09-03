from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"missing refine anchor: {label}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# `day_key` already has mapped_column(index=True); do not declare the same
# SQLite index a second time in __table_args__.
patch(
    "app/db.py",
    '        Index("ux_cloud_days_profile_day_provider", "profile_id", "day_key", "provider", unique=True),\n        Index("ix_cloud_days_day_key", "day_key"),\n',
    '        Index("ux_cloud_days_profile_day_provider", "profile_id", "day_key", "provider", unique=True),\n',
    "duplicate CloudDay day index",
)

# Resolve the profile/day before opening the write transaction; avoids nesting
# db_session() while updating the Gofile day counter.
patch(
    "app/workers.py",
    '''                    if result.provider == "gofile" and gofile_folder_id:\n                        with db_session() as db:\n                            profile_id, day_key, _title, _organize = self._cloud_day_spec(rec)\n                            if profile_id is not None:\n                                day = db.scalar(select(CloudDay).where(\n                                    CloudDay.profile_id == profile_id,\n                                    CloudDay.day_key == day_key,\n                                    CloudDay.provider == "gofile",\n                                ))\n                                if day:\n                                    day.file_count = int(day.file_count or 0) + 1\n                                    day.updated_at = utcnow()\n''',
    '''                    if result.provider == "gofile" and gofile_folder_id:\n                        profile_id, day_key, _title, _organize = self._cloud_day_spec(rec)\n                        if profile_id is not None:\n                            with db_session() as db:\n                                day = db.scalar(select(CloudDay).where(\n                                    CloudDay.profile_id == profile_id,\n                                    CloudDay.day_key == day_key,\n                                    CloudDay.provider == "gofile",\n                                ))\n                                if day:\n                                    day.file_count = int(day.file_count or 0) + 1\n                                    day.updated_at = utcnow()\n''',
    "nested Gofile db session",
)

# Existing release consistency tests intentionally pin the current release.
for path in ("tests/test_v284_pulse_media.py", "tests/test_version_consistency.py"):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if '"2.8.5"' not in text:
        raise SystemExit(f"missing 2.8.5 assertion in {path}")
    target.write_text(text.replace('"2.8.5"', '"2.8.6"'), encoding="utf-8")

print("v2.8.6 staging refinements applied")
