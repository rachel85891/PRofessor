"""Port for the files of a new assignment: its intake, task text and reference solution."""

from pathlib import Path
from typing import Protocol

from hw_checker.domain.authoring import AssignmentIntake


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
