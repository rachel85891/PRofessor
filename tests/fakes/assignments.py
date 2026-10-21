"""In-memory fake for the AssignmentSource port."""

from collections.abc import Iterable

from hw_checker.domain import Assignment
from hw_checker.domain.errors import InvalidAssignmentError


class FakeAssignmentSource:
    """Returns the given assignments, or raises the given error, and counts the calls."""

    def __init__(
        self, assignments: Iterable[Assignment] = (), error: InvalidAssignmentError | None = None
    ) -> None:
        self._assignments = tuple(assignments)
        self._error = error
        self.calls = 0

    def load_all(self) -> tuple[Assignment, ...]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._assignments
