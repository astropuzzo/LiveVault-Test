"""Live-start forecasting from observed live sessions.

Model (per creator):
- 168 hour-of-week bins in the display time zone.
- Every past day in the window contributes with weight 0.5 ** (age / half_life),
  so habits that changed recently dominate older ones.
- For each bin we accumulate the weighted time online, the weighted number of
  session starts and the weighted exposure (how many times that hour was
  observed at all). Neighbouring hours are blended with a circular kernel so a
  creator starting at 20:55 or 21:05 reads as one habit.
- Probabilities use a Beta prior (few observations → conservative estimate).
- Start probability over the next hours combines the per-hour start chances;
  the forecast also reports the most likely start hour, a typical duration and
  a confidence level from the effective number of active days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

HOURS = 168
PSEUDO_DAYS = 3.0
KERNEL = (0.2, 0.6, 0.2)


@dataclass
class Forecast:
    probability_24h: float
    next_start_at: datetime | None
    next_start_probability: float
    peak_hour: int | None
    typical_minutes: int | None
    confidence: str
    active_days: float

    def as_dict(self) -> dict:
        return {
            "probability_24h": round(self.probability_24h, 3),
            "next_start_at": self.next_start_at.astimezone(timezone.utc).isoformat() if self.next_start_at else None,
            "next_start_probability": round(self.next_start_probability, 3),
            "peak_hour": self.peak_hour,
            "typical_minutes": self.typical_minutes,
            "confidence": self.confidence,
            "active_days": round(self.active_days, 1),
        }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _smooth(values: list[float]) -> list[float]:
    left, center, right = KERNEL
    return [left * values[(i - 1) % HOURS] + center * values[i] + right * values[(i + 1) % HOURS] for i in range(HOURS)]


def _slot(moment: datetime) -> int:
    return moment.weekday() * 24 + moment.hour


def forecast(sessions: list[tuple[datetime, datetime | None]], *, now: datetime, tz: ZoneInfo,
             window_days: int = 90, half_life_days: float = 21.0, horizon_hours: int = 48) -> Forecast:
    now = _aware(now)
    local_now = now.astimezone(tz)
    window_start = now - timedelta(days=window_days)
    online = [0.0] * HOURS
    starts = [0.0] * HOURS
    exposure = [0.0] * HOURS
    weight = lambda moment: 0.5 ** (max(0.0, (now - moment).total_seconds()) / 86400 / half_life_days)

    # Exposure: every observed hour was an opportunity to be live. Observation
    # starts at the first known session, not at the start of the window.
    known = [_aware(start) for start, _ in sessions if start]
    observed_from = max(window_start, min(known) - timedelta(days=1)) if known else window_start
    cursor = observed_from.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    while cursor < local_now:
        exposure[_slot(cursor)] += weight(cursor)
        cursor += timedelta(hours=1)

    durations: list[tuple[float, float]] = []
    active_days: dict[str, float] = {}
    for start, end in sessions:
        start = _aware(start)
        end = _aware(end) if end else now
        if end <= window_start or end <= start:
            continue
        start = max(start, window_start)
        w = weight(start)
        local_start = start.astimezone(tz)
        starts[_slot(local_start)] += w
        durations.append(((end - start).total_seconds() / 60, w))
        active_days[local_start.date().isoformat()] = max(active_days.get(local_start.date().isoformat(), 0.0), w)
        cursor = start
        while cursor < end:
            next_hour = (cursor.astimezone(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
            piece_end = min(end, next_hour)
            online[_slot(cursor.astimezone(tz))] += weight(cursor) * (piece_end - cursor).total_seconds() / 3600
            cursor = piece_end

    online = _smooth(online)
    # Start chance per hour: the hour itself counts fully, its neighbours half,
    # so 20:55 and 21:05 read as one habit without diluting the peak.
    effective_days = sum(active_days.values())

    def shrink(raw: float, base: float) -> float:
        # Few active days → pull the estimate towards a neutral base rate.
        return (effective_days * raw + PSEUDO_DAYS * base) / (effective_days + PSEUDO_DAYS)

    def start_chance(i: int) -> float:
        hits = starts[i] + 0.5 * (starts[(i - 1) % HOURS] + starts[(i + 1) % HOURS])
        raw = min(0.97, hits / exposure[i]) if exposure[i] > 0 else 0.0
        return shrink(raw, 0.02)
    p_online = [(online[i] + 0.1) / (exposure[i] + 1.1) for i in range(HOURS)]
    hour = local_now.replace(minute=0, second=0, microsecond=0)
    upcoming = [hour + timedelta(hours=step) for step in range(1, horizon_hours + 1)]
    chances = [start_chance(_slot(moment)) for moment in upcoming]
    next_start_at = None
    next_probability = 0.0
    for moment, chance in zip(upcoming, chances):
        if chance >= 0.18 and chance > next_probability + 0.02:
            if next_start_at is not None and chance < next_probability * 1.25 and moment - next_start_at > timedelta(hours=2):
                break  # keep the first clear habit instead of a marginally stronger later one
            next_start_at, next_probability = moment, chance
        elif next_start_at is not None and moment - next_start_at > timedelta(hours=2):
            break
    # Expected starts in the next 24 h relative to how often that span was observed.
    expected = sum(starts[_slot(moment)] for moment in upcoming[:24])
    observed = sum(exposure[_slot(moment)] for moment in upcoming[:24]) / 24
    raw_24h = min(0.97, expected / observed) if observed > 0 else 0.0
    probability_24h = shrink(raw_24h, 0.25) if durations else 0.0
    peak = max(range(HOURS), key=lambda i: p_online[i]) if effective_days else None
    typical = None
    if durations:
        total = sum(w for _, w in durations)
        ordered = sorted(durations)
        running = 0.0
        for minutes, w in ordered:
            running += w
            if running >= total / 2:
                typical = int(round(minutes))
                break
        typical = typical or int(median(m for m, _ in durations))
    confidence = "alta" if effective_days >= 8 else "media" if effective_days >= 3 else "bassa"
    return Forecast(
        probability_24h=probability_24h,
        next_start_at=next_start_at.astimezone(timezone.utc) if next_start_at else None,
        next_start_probability=next_probability,
        peak_hour=(peak % 24) if peak is not None else None,
        typical_minutes=typical,
        confidence=confidence,
        active_days=effective_days,
    )


def rank_upcoming(forecasts: list[dict], limit: int = 8) -> list[dict]:
    """Soonest likely starts first, weighted by probability."""
    usable = [row for row in forecasts if row.get("next_start_at") and row.get("next_start_probability", 0) >= 0.18]
    return sorted(usable, key=lambda row: (row["next_start_at"], -row["next_start_probability"]))[:limit]

