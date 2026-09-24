from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.predictions import forecast, rank_upcoming

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=ROME).astimezone(timezone.utc)  # Tuesday 10:00


def nightly(days: int, hour: int = 21, minutes: int = 120, weekdays=range(7)):
    rows = []
    for back in range(1, days + 1):
        day = (NOW.astimezone(ROME) - timedelta(days=back)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if day.weekday() in weekdays:
            rows.append((day.astimezone(timezone.utc), (day + timedelta(minutes=minutes)).astimezone(timezone.utc)))
    return rows


def test_regular_creator_predicts_evening_start_with_high_confidence():
    result = forecast(nightly(40), now=NOW, tz=ROME)
    assert result.confidence == "alta"
    assert result.next_start_at.astimezone(ROME).hour == 21
    assert result.next_start_at.astimezone(ROME).date() == NOW.astimezone(ROME).date()
    assert result.next_start_probability > 0.5
    assert result.probability_24h > 0.8
    assert result.typical_minutes == 120
    assert result.peak_hour in {21, 22}


def test_weekday_habit_is_respected():
    # Only Fridays: from Tuesday the next likely start is Friday evening.
    result = forecast(nightly(60, hour=22, weekdays={4}), now=NOW, tz=ROME, horizon_hours=96)
    assert result.next_start_at is not None
    assert result.next_start_at.astimezone(ROME).weekday() == 4


def test_recent_habits_outweigh_old_ones():
    old = [(s - timedelta(days=45), e - timedelta(days=45)) for s, e in nightly(30, hour=15)]
    recent = nightly(20, hour=21)
    result = forecast(old + recent, now=NOW, tz=ROME)
    assert result.next_start_at.astimezone(ROME).hour == 21


def test_sparse_history_stays_conservative():
    result = forecast(nightly(2), now=NOW, tz=ROME)
    assert result.confidence == "bassa"
    assert result.probability_24h < 0.6
    assert forecast([], now=NOW, tz=ROME).probability_24h == 0.0


def test_upcoming_ranking_orders_by_time_then_probability():
    rows = [
        {"display_name": "b", "next_start_at": "2026-09-22T20:00:00+00:00", "next_start_probability": 0.4},
        {"display_name": "a", "next_start_at": "2026-09-22T18:00:00+00:00", "next_start_probability": 0.3},
        {"display_name": "c", "next_start_at": None, "next_start_probability": 0.0},
        {"display_name": "d", "next_start_at": "2026-09-22T17:00:00+00:00", "next_start_probability": 0.05},
    ]
    assert [row["display_name"] for row in rank_upcoming(rows)] == ["a", "b"]
