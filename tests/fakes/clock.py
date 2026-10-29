"""Fake for the Clock port."""

from datetime import UTC, datetime, timedelta

DEFAULT_NOW = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


class FakeClock:
    """Returns a fixed time that tests can move forward."""

    def __init__(self, now: datetime = DEFAULT_NOW) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
