"""Port for the current time, so services stay deterministic in tests."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime. Raises nothing."""
        ...
