from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat().replace("+00:00", "Z") if value else None


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged: list[list[datetime]] = [[ordered[0][0], ordered[0][1]]]
    for start, end in ordered[1:]:
        current = merged[-1]
        if start <= current[1]:
            if end > current[1]:
                current[1] = end
        else:
            merged.append([start, end])
    return [(row[0], row[1]) for row in merged]


def _interval_total(intervals: list[tuple[datetime, datetime]]) -> float:
    return sum(max(0.0, (end - start).total_seconds()) for start, end in intervals)


def _overlap_total(base: list[tuple[datetime, datetime]], mask: list[tuple[datetime, datetime]]) -> float:
    if not base or not mask:
        return 0.0
    total = 0.0
    j = 0
    for start, end in base:
        while j < len(mask) and mask[j][1] <= start:
            j += 1
        k = j
        while k < len(mask) and mask[k][0] < end:
            overlap_start = max(start, mask[k][0])
            overlap_end = min(end, mask[k][1])
            if overlap_end > overlap_start:
                total += (overlap_end - overlap_start).total_seconds()
            k += 1
    return total


def _accumulate_interval(start: datetime, end: datetime, daily: dict, hourly: list[dict], key: str) -> float:
    total = 0.0
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        stop = min(end, next_hour)
        seconds = max(0.0, (stop - cursor).total_seconds())
        if seconds:
            day_key = cursor.date().isoformat()
            if day_key in daily:
                daily[day_key][key] += seconds
            hourly[cursor.hour][key] += seconds
            total += seconds
        cursor = stop
    return total


def build_activity_statistics(*, sources: Iterable, profiles: Iterable, live_sessions: Iterable, recordings: Iterable, days: int = 30, now: datetime | None = None) -> dict:
    days = max(1, min(int(days), 365))
    now = _aware(now) or datetime.now(timezone.utc)
    window_start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    source_rows = list(sources)
    profile_rows = list(profiles)
    source_map = {int(row.id): row for row in source_rows}
    profiles_by_id = {int(row.id): row for row in profile_rows}

    source_to_profile: dict[int, int] = {}
    representative: dict[int, int] = {}
    for source in source_rows:
        if source.profile_id is None:
            continue
        profile_id = int(source.profile_id)
        source_to_profile[int(source.id)] = profile_id
        if profile_id not in representative or not getattr(source, "archived", False):
            representative[profile_id] = int(source.id)

    daily = {}
    cursor_day = window_start
    while cursor_day.date() <= now.date():
        daily[cursor_day.date().isoformat()] = {"online_seconds": 0.0, "recorded_seconds": 0.0, "recording_count": 0, "recording_bytes": 0, "uploaded_count": 0}
        cursor_day += timedelta(days=1)
    hourly = [{"hour": hour, "online_seconds": 0.0, "recorded_seconds": 0.0} for hour in range(24)]

    live_intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    exact_intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    recording_intervals: dict[int, list[tuple[datetime, datetime]]] = defaultdict(list)
    recording_session_keys: dict[int, set[tuple[int, str]]] = defaultdict(set)
    recording_totals: dict[int, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0, "uploaded": 0, "failed": 0, "local": 0})
    online_now_profiles: set[int] = set()
    exact_tracking_started_at: datetime | None = None

    for session in live_sessions:
        source_id = int(session.source_id)
        profile_id = source_to_profile.get(source_id)
        if source_id not in source_map or profile_id is None:
            continue
        start = _aware(session.started_at)
        end = _aware(session.ended_at) or now
        if start is None or end <= window_start or start > now:
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, now)
        if clipped_end <= clipped_start:
            continue
        live_intervals[profile_id].append((clipped_start, clipped_end))
        if session.ended_at is None:
            online_now_profiles.add(profile_id)
        if getattr(session, "origin", "probe") != "recording_backfill":
            exact_intervals[profile_id].append((clipped_start, clipped_end))
            if exact_tracking_started_at is None or start < exact_tracking_started_at:
                exact_tracking_started_at = start

    for recording in recordings:
        source_id = int(recording.source_id)
        profile_id = source_to_profile.get(source_id)
        if source_id not in source_map or profile_id is None:
            continue
        end = _aware(recording.finalized_at)
        start = _aware(recording.started_at)
        duration = float(getattr(recording, "duration_seconds", 0.0) or 0.0)
        if end is None:
            continue
        if start is None or start > end:
            start = end - timedelta(seconds=max(0.0, duration))
        if end <= window_start or start > now:
            continue
        clipped_start = max(start, window_start)
        clipped_end = min(end, now)
        if clipped_end <= clipped_start:
            continue
        recording_intervals[profile_id].append((clipped_start, clipped_end))
        recording_session_keys[profile_id].add((source_id, str(getattr(recording, "session_id", ""))))
        totals = recording_totals[profile_id]
        totals["count"] += 1
        totals["bytes"] += max(0, int(getattr(recording, "size_bytes", 0) or 0))
        status = str(getattr(recording, "upload_status", "") or "")
        totals["uploaded"] += int(status == "uploaded")
        totals["failed"] += int(status in {"failed", "integrity_failed", "waiting_config"})
        totals["local"] += int(not bool(getattr(recording, "local_deleted", False)))
        day_key = clipped_end.date().isoformat()
        if day_key in daily:
            daily[day_key]["recording_count"] += 1
            daily[day_key]["recording_bytes"] += max(0, int(getattr(recording, "size_bytes", 0) or 0))
            daily[day_key]["uploaded_count"] += int(status == "uploaded")

    per_profile: dict[int, dict] = {}
    online_seconds = 0.0
    recorded_seconds = 0.0
    exact_seconds = 0.0
    live_session_count = 0
    longest_live_seconds = 0.0

    for profile_id in profiles_by_id:
        merged_live = _merge_intervals(live_intervals.get(profile_id, []))
        merged_exact = _merge_intervals(exact_intervals.get(profile_id, []))
        merged_recorded = _merge_intervals(recording_intervals.get(profile_id, []))

        profile_online = _interval_total(merged_live)
        profile_recorded = _interval_total(merged_recorded)
        profile_exact = _overlap_total(merged_live, merged_exact)
        profile_days: set[str] = set()
        for start, end in merged_live:
            live_session_count += 1
            longest_live_seconds = max(longest_live_seconds, (end - start).total_seconds())
            _accumulate_interval(start, end, daily, hourly, "online_seconds")
            day_cursor = start.date()
            last_day = (end - timedelta(microseconds=1)).date()
            while day_cursor <= last_day:
                profile_days.add(day_cursor.isoformat())
                day_cursor += timedelta(days=1)
        for start, end in merged_recorded:
            _accumulate_interval(start, end, daily, hourly, "recorded_seconds")

        online_seconds += profile_online
        recorded_seconds += profile_recorded
        exact_seconds += profile_exact
        per_profile[profile_id] = {
            "online_seconds": profile_online,
            "recorded_seconds": profile_recorded,
            "days_online": len(profile_days),
            "live_sessions": len(merged_live),
            "recording_sessions": len(recording_session_keys.get(profile_id, set())),
        }

    top_creators = []
    for profile_id, profile in profiles_by_id.items():
        bucket = per_profile.get(profile_id, {})
        online = float(bucket.get("online_seconds", 0.0))
        recorded = float(bucket.get("recorded_seconds", 0.0))
        coverage = min(100.0, recorded / online * 100.0) if online > 0 else 0.0
        top_creators.append({
            "profile_id": profile_id,
            "representative_source_id": representative.get(profile_id),
            "display_name": str(profile.display_name),
            "online_seconds": round(online, 2),
            "recorded_seconds": round(recorded, 2),
            "days_online": int(bucket.get("days_online", 0)),
            "live_sessions": int(bucket.get("live_sessions", 0)),
            "recording_sessions": int(bucket.get("recording_sessions", 0)),
            "coverage_percent": round(coverage, 1),
            "online_now": profile_id in online_now_profiles,
            "recording_count": recording_totals[profile_id]["count"],
            "recording_bytes": recording_totals[profile_id]["bytes"],
            "uploaded_count": recording_totals[profile_id]["uploaded"],
            "failed_count": recording_totals[profile_id]["failed"],
            "local_count": recording_totals[profile_id]["local"],
        })
    top_creators.sort(key=lambda row: (row["online_seconds"], row["recorded_seconds"], row["display_name"].lower()), reverse=True)

    coverage = min(100.0, recorded_seconds / online_seconds * 100.0) if online_seconds > 0 else 0.0
    estimated_seconds = max(0.0, online_seconds - exact_seconds)
    daily_rows = [
        {"date": key, "online_seconds": round(value["online_seconds"], 2), "recorded_seconds": round(value["recorded_seconds"], 2), "recording_count": value["recording_count"], "recording_bytes": value["recording_bytes"], "uploaded_count": value["uploaded_count"]}
        for key, value in daily.items()
    ]
    hourly_rows = [
        {"hour": row["hour"], "online_seconds": round(row["online_seconds"], 2), "recorded_seconds": round(row["recorded_seconds"], 2)}
        for row in hourly
    ]
    return {
        "days": days,
        "range_start": _iso(window_start),
        "range_end": _iso(now),
        "summary": {
            "creator_count": len(profiles_by_id),
            "online_now": len(online_now_profiles),
            "online_seconds": round(online_seconds, 2),
            "recorded_seconds": round(recorded_seconds, 2),
            "days_online": sum(1 for row in daily_rows if row["online_seconds"] > 0),
            "live_sessions": live_session_count,
            "recording_sessions": sum(len(rows) for rows in recording_session_keys.values()),
            "longest_live_seconds": round(longest_live_seconds, 2),
            "coverage_percent": round(coverage, 1),
            "exact_online_seconds": round(exact_seconds, 2),
            "estimated_online_seconds": round(estimated_seconds, 2),
            "exact_tracking_started_at": _iso(exact_tracking_started_at),
            "recording_count": sum(row["recording_count"] for row in top_creators),
            "recording_bytes": sum(row["recording_bytes"] for row in top_creators),
            "uploaded_count": sum(row["uploaded_count"] for row in top_creators),
            "failed_count": sum(row["failed_count"] for row in top_creators),
            "local_count": sum(row["local_count"] for row in top_creators),
        },
        "daily": daily_rows,
        "hourly": hourly_rows,
        "top_creators": top_creators,
    }
