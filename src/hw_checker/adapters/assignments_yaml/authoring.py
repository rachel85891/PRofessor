"""YamlAuthoringFiles: the files of a new assignment in `assignments/<id>/` (design §8.3)."""

import logging
from pathlib import Path

from pydantic import ValidationError

from hw_checker.adapters.assignments_yaml.rules import describe, read_mapping
from hw_checker.domain.authoring import AssignmentIntake
from hw_checker.domain.errors import AuthoringError, InvalidAssignmentError

logger = logging.getLogger(__name__)

INTAKE_FILE = "intake.yaml"
TASK_FILE = "task.md"
REFERENCE_FOLDER = "reference"


class YamlAuthoringFiles:
    """Implements `AuthoringFiles` over the assignments folder."""

    def __init__(self, assignments_dir: Path) -> None:
        self._dir = assignments_dir

    def exists(self, assignment_id: str) -> bool:
        """See `AuthoringFiles.exists`."""
        return (self._dir / assignment_id).exists()

    def create(self, assignment_id: str, intake_text: str) -> Path:
        """See `AuthoringFiles.create`."""
        folder = self._dir / assignment_id
        try:
            folder.mkdir(parents=True, exist_ok=False)
            (folder / INTAKE_FILE).write_bytes(intake_text.encode("utf-8"))
            (folder / TASK_FILE).write_bytes(b"")
        except FileExistsError as error:
            raise AuthoringError(f"The assignment folder {folder} already exists.") from error
        except OSError as error:
            raise AuthoringError(f"Cannot create {folder}: {error}") from error
        logger.info("Created the assignment folder %s", folder)
        return folder

    def intakes(self) -> tuple[AssignmentIntake, ...]:
        """See `AuthoringFiles.intakes`."""
        if not self._dir.is_dir():
            return ()
        problems: list[str] = []
        found = [
            intake
            for folder in sorted(self._dir.iterdir())
            if (folder / INTAKE_FILE).is_file()
            and (intake := self._parse(folder, problems)) is not None
        ]
        if problems:
            raise _invalid(problems)
        return tuple(sorted(found, key=lambda intake: intake.order))

    def load_intake(self, assignment_id: str) -> AssignmentIntake:
        """See `AuthoringFiles.load_intake`."""
        folder = self._dir / assignment_id
        if not (folder / INTAKE_FILE).is_file():
            raise AuthoringError(
                f"{folder / INTAKE_FILE} is missing. Create it with: "
                f"python -m hw_checker assignment new {assignment_id}"
            )
        problems: list[str] = []
        intake = self._parse(folder, problems)
        if intake is None:
            raise _invalid(problems)
        return intake

    def _parse(self, folder: Path, problems: list[str]) -> AssignmentIntake | None:
        label = f"{self._dir.name}/{folder.name}/{INTAKE_FILE}"
        data = read_mapping(folder / INTAKE_FILE, label, problems, required=True)
        if data is None:
            return None
        try:
            intake = AssignmentIntake.model_validate(data)
        except ValidationError as error:
            problems.extend(f"{label}{describe(item)}" for item in error.errors())
            return None
        if intake.id != folder.name:
            problems.append(
                f"{label}: id '{intake.id}' does not match the folder name '{folder.name}'."
            )
            return None
        return intake


def _invalid(problems: list[str]) -> InvalidAssignmentError:
    return InvalidAssignmentError(
        f"Invalid intake ({len(problems)} problem{'' if len(problems) == 1 else 's'}). "
        "See assignments/README.md, 'Adding an assignment'.\n"
        + "\n".join(f"- {problem}" for problem in problems)
    )
