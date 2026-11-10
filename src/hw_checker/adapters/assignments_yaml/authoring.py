"""YamlAuthoringFiles: the files of a new assignment in `assignments/<id>/` (design §8.3)."""

import logging
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from hw_checker.adapters.assignments_yaml.rules import (
    COURSE_FILE,
    RULES_FILE,
    describe,
    read_mapping,
)
from hw_checker.domain.authoring import AssignmentIntake, DataFile
from hw_checker.domain.errors import AuthoringError, InvalidAssignmentError

logger = logging.getLogger(__name__)

REQUIREMENTS_FILE = "requirements.yaml"
INTAKE_FILE = "intake.yaml"
TASK_FILE = "task.md"
REFERENCE_FOLDER = "reference"


class YamlAuthoringFiles:
    """Implements `AuthoringFiles` over the assignments and course folders."""

    def __init__(self, assignments_dir: Path, course_dir: Path) -> None:
        self._dir = assignments_dir
        self._course_dir = course_dir

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

    def task_text(self, assignment_id: str) -> str:
        """See `AuthoringFiles.task_text`."""
        path = self._dir / assignment_id / TASK_FILE
        text = _read(path, f"{self._dir.name}/{assignment_id}/{TASK_FILE}")
        if not text.strip():
            raise AuthoringError(f"{path} is empty: write the task's text there first.")
        return text

    def course_files(self) -> tuple[DataFile, ...]:
        """See `AuthoringFiles.course_files`."""
        name = self._course_dir.name
        return tuple(
            DataFile(label=f"{name}/{file}", text=_read(self._course_dir / file, file))
            for file in (COURSE_FILE, RULES_FILE)
            if (self._course_dir / file).is_file()
        )

    def assignment_files(self, assignment_ids: Sequence[str]) -> tuple[DataFile, ...]:
        """See `AuthoringFiles.assignment_files`."""
        found: list[DataFile] = []
        for assignment_id in assignment_ids:
            for file in (REQUIREMENTS_FILE, RULES_FILE):
                path = self._dir / assignment_id / file
                label = f"{self._dir.name}/{assignment_id}/{file}"
                if path.is_file():
                    found.append(DataFile(label=label, text=_read(path, label)))
        return tuple(found)

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


def _read(path: Path, label: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError as error:
        raise AuthoringError(f"{label} is missing.") from error
    except UnicodeDecodeError as error:
        raise AuthoringError(f"{label} is not UTF-8 text; save it with UTF-8.") from error
    except OSError as error:
        raise AuthoringError(f"Cannot read {label}: {error}") from error


def _invalid(problems: list[str]) -> InvalidAssignmentError:
    return InvalidAssignmentError(
        f"Invalid intake ({len(problems)} problem{'' if len(problems) == 1 else 's'}). "
        "See assignments/README.md, 'Adding an assignment'.\n"
        + "\n".join(f"- {problem}" for problem in problems)
    )
