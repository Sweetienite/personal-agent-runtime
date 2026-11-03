"""Time source. Injectable so cooldowns and heartbeats can be tested without sleeping."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def iso(dt: datetime) -> str:
    """UTC, millisecond precision, sortable as text: 2026-10-14T03:04:05.678Z."""
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def iso(self) -> str:
        return iso(self.now())


class FakeClock(Clock):
    def __init__(self, start: "datetime | None" = None) -> None:
        self._now = start or datetime(2026, 10, 14, 3, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)
