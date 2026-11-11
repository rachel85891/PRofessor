"""Port for the files of a new assignment: its intake, task text and reference solution."""

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Protocol

from hw_checker.domain import Assignment, Course
from hw_checker.domain.authoring import (
    AssignmentIntake,
    AuthorReply,
    DataFile,
    DraftFiles,
    EditedDraft,
)


class AuthoringFiles(Protocol):
    """Reads and writes `assignments/<id>/` (`intake.yaml`, `task.md`, `reference/` and the
    drafted files) and the private authoring reports."""

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

    def reference_dir(self, assignment_id: str) -> Path | None:
        """The assignment's `reference/` folder (a reference solution); None when it is
        missing or empty. Raises nothing."""
        ...

    def draft_files(
        self, intake: AssignmentIntake, reply: AuthorReply, approved_on: date | None
    ) -> DraftFiles:
        """The assignment's `requirements.yaml` and `review_rules.yaml` for `reply`: the
        intake's basics and the drafted items, with a first comment line that says the
        files are a draft, or the day they were approved (`approved_on`). Raises nothing."""
        ...

    def catalog_with(
        self, assignment_id: str, files: DraftFiles
    ) -> tuple[Course, tuple[Assignment, ...]]:
        """The course and every assignment as the loader reads them with `files` in the
        assignment's folder, without writing them there.

        Raises:
            InvalidAssignmentError: the data is invalid with them (`problems` lists all).
        """
        ...

    def parse_files(self, assignment_id: str, files: DraftFiles) -> EditedDraft:
        """Read `files` (the assignment's drafted files, maybe edited by hand) back as items.
        An item added without `origin` and `status` is read as the developer's draft.

        Raises:
            InvalidAssignmentError: a file is not valid YAML, or an item does not fit its
                model (`problems` lists all).
        """
        ...

    def read_files(self, assignment_id: str) -> DraftFiles | None:
        """The assignment's two files as they are now; None when one is missing.

        Raises:
            AuthoringError: a file cannot be read.
        """
        ...

    def write_files(self, assignment_id: str, files: DraftFiles) -> tuple[Path, ...]:
        """Write `files` to the assignment's folder; return the paths written.

        Raises:
            AuthoringError: a file cannot be written.
        """
        ...

    def remove_files(self, assignment_id: str) -> None:
        """Remove the assignment's `requirements.yaml` and `review_rules.yaml`.

        Raises:
            AuthoringError: a file cannot be removed.
        """
        ...

    def write_report(self, assignment_id: str, report: str, reply: AuthorReply) -> Path:
        """Write `authoring_report.md` and the draft as JSON to the private authoring
        folder; return the report's path.

        Raises:
            AuthoringError: the files cannot be written.
        """
        ...

    def clone_dirs(self) -> tuple[Path, ...]:
        """The working copies the rounds left in the workspace's clones folder, for the
        preview; their content is not read here. Raises nothing."""
        ...
