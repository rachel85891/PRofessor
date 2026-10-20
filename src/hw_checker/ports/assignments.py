"""Port for loading assignments and their requirements."""

from typing import Protocol

from hw_checker.domain import Assignment


class AssignmentSource(Protocol):
    """Loads every known assignment; selecting assignments 1..N is a service's job."""

    def load_all(self) -> tuple[Assignment, ...]:
        """Return every assignment, including `_general`, ordered by `order`.

        Raises:
            InvalidAssignmentError: assignment data exists but is invalid.
        """
        ...
