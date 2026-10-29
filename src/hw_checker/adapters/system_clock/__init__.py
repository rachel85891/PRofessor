"""System clock adapter: implements the Clock port with the machine's clock."""

from datetime import UTC, datetime


class SystemClock:
    """The current time in UTC."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        return datetime.now(UTC)
