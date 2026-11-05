"""In-memory fake for the AssignmentSource port."""

from collections.abc import Iterable

from hw_checker.domain import Assignment, Course
from hw_checker.domain.errors import InvalidAssignmentError
from tests.unit.domain.builders import course as default_course


class FakeAssignmentSource:
    """Returns the given assignments and course, or raises the given error, and counts the
    calls to `load_all`."""

    def __init__(
        self,
        assignments: Iterable[Assignment] = (),
        error: InvalidAssignmentError | None = None,
        course: Course | None = None,
    ) -> None:
        self._assignments = tuple(assignments)
        self._error = error
        self._course = course or default_course()
        self.calls = 0

    def load_all(self) -> tuple[Assignment, ...]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._assignments

    def load_course(self) -> Course:
        if self._error is not None:
            raise self._error
        return self._course
