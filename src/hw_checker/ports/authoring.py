"""Port for the files of a new assignment: its intake, task text and reference solution."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from hw_checker.domain.authoring import AssignmentIntake, DataFile


class AuthoringFiles(Protocol):
    """Reads and writes `assignments/<id>/`: `intake.yaml`, `task.md` and `reference/`."""

    def exists(self, assignment_id: str) -> bool:
        """Whether the folder `assignments/<id>` exists. Raises nothing."""
        ...

    def create(self, assignment_id: str, intake_text: str) -> Path:
        """Create `assignments/<id>/` with `intake_text` as `intake.yaml` and an empty
        `task.md`; return the folder.

        Raises:
            AuthoringError: the folder exists, or cannot be written.
        """
        ...

    def intakes(self) -> tuple[AssignmentIntake, ...]:
        """Every assignment folder's intake, ordered by `order`; folders without one are
        skipped.

        Raises:
            InvalidAssignmentError: an intake is invalid.
        """
        ...

    def load_intake(self, assignment_id: str) -> AssignmentIntake:
        """The intake of `assignment_id`.

        Raises:
            AuthoringError: the folder or its `intake.yaml` is missing.
            InvalidAssignmentError: the intake is invalid (every problem listed).
        """
        ...

    def task_text(self, assignment_id: str) -> str:
        """The text of `assignments/<id>/task.md`.

        Raises:
            AuthoringError: the file is missing, empty or cannot be read.
        """
        ...

    def course_files(self) -> tuple[DataFile, ...]:
        """The course's `course.yaml` and `review_rules.yaml`.

        Raises:
            AuthoringError: a file cannot be read.
        """
        ...

    def assignment_files(self, assignment_ids: Sequence[str]) -> tuple[DataFile, ...]:
        """Each assignment's `requirements.yaml`, then its `review_rules.yaml` when it has
        one, in the order of `assignment_ids`.

        Raises:
            AuthoringError: a file cannot be read.
        """
        ...
