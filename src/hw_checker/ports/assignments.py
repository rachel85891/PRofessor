"""Port for loading the course profile, the assignments, their requirements and their
review rules."""

from typing import Protocol

from hw_checker.domain import Assignment, Course


class AssignmentSource(Protocol):
    """Loads every known assignment; selecting assignments 1..N is a service's job."""

    def load_all(self) -> tuple[Assignment, ...]:
        """Return every assignment, including `_general`, ordered by `order`, each with its
        review rules.

        Raises:
            InvalidAssignmentError: assignment data exists but is invalid.
        """
        ...

    def load_course(self) -> Course:
        """Return the course profile and the course's own review rules (ADR 0023).

        Raises:
            InvalidAssignmentError: the course or assignment data is missing or invalid.
        """
        ...
