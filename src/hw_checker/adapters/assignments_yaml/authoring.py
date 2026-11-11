"""YamlAuthoringFiles: the files of a new assignment in `assignments/<id>/` (design §8.3)."""

import json
import logging
import shutil
import tempfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from hw_checker.adapters.assignments_yaml.rules import (
    COURSE_FILE,
    RULES_FILE,
    describe,
    read_mapping,
)
from hw_checker.adapters.assignments_yaml.source import YamlAssignmentSource
from hw_checker.domain import Assignment, Course, FixExample, ReviewRule
from hw_checker.domain.authoring import (
    AssignmentIntake,
    AuthorReply,
    DataFile,
    DraftFiles,
    EditedDraft,
    RequirementSpec,
)
from hw_checker.domain.errors import AuthoringError, InvalidAssignmentError

logger = logging.getLogger(__name__)

REQUIREMENTS_FILE = "requirements.yaml"
INTAKE_FILE = "intake.yaml"
TASK_FILE = "task.md"
REFERENCE_FOLDER = "reference"
REPORT_FILE = "authoring_report.md"
DRAFT_FILE = "draft.json"
SCHEMA_LINE = "# yaml-language-server: $schema=../../course/schemas/{schema}"
DRAFT_LINE = (
    "# A draft of the authoring graph (design §8.3): a round refuses {id} until it is\n"
    "# approved with python -m hw_checker assignment approve {id}"
)
APPROVED_LINE = "# Approved on {day} through the authoring graph (design §8.3); origins kept."
HAND_WRITTEN = {"origin": {"source": "developer", "quote": "added by hand"}, "status": "draft"}
"""What an item added by hand, without its own origin and status, is read with."""
LEADING_KEYS = ("id", "applies_to", "kind", "text")
"""Written first in every item, as in the hand-written files."""


class YamlAuthoringFiles:
    """Implements `AuthoringFiles` over the assignments and course folders. Reports go to
    `work_dir/<id>/`; the preview's working copies are the folders in `clones_dir`."""

    def __init__(
        self,
        assignments_dir: Path,
        course_dir: Path,
        work_dir: Path | None = None,
        clones_dir: Path | None = None,
    ) -> None:
        self._dir = assignments_dir
        self._course_dir = course_dir
        self._work_dir = work_dir
        self._clones_dir = clones_dir

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

    def reference_dir(self, assignment_id: str) -> Path | None:
        """See `AuthoringFiles.reference_dir`."""
        folder = self._dir / assignment_id / REFERENCE_FOLDER
        return folder if folder.is_dir() and any(folder.iterdir()) else None

    def draft_files(
        self, intake: AssignmentIntake, reply: AuthorReply, approved_on: date | None
    ) -> DraftFiles:
        """See `AuthoringFiles.draft_files`."""
        header = (
            DRAFT_LINE.format(id=intake.id)
            if approved_on is None
            else APPROVED_LINE.format(day=approved_on.isoformat())
        )
        ends_when = intake.ends_when
        requirements: dict[str, Any] = {
            "id": intake.id,
            "order": intake.order,
            "title": intake.title,
            "taught_techniques": list(intake.taught),
            "lenient_notes": list(reply.lenient_notes),
            "ends_when": ends_when.model_dump(mode="json") if ends_when else None,
            "requirements": [_fields(item, RequirementSpec) for item in reply.requirements],
        }
        rules = {
            "rules": [_fields(rule, ReviewRule) for rule in reply.rules],
            "examples": [_fields(example, FixExample) for example in reply.examples],
        }
        kept = {key: value for key, value in requirements.items() if value or key == "requirements"}
        return DraftFiles(
            requirements=_yaml_file(None, header, kept),
            review_rules=_yaml_file("review_rules.schema.json", header, rules),
        )

    def catalog_with(
        self, assignment_id: str, files: DraftFiles
    ) -> tuple[Course, tuple[Assignment, ...]]:
        """See `AuthoringFiles.catalog_with`."""
        with tempfile.TemporaryDirectory(prefix="hw-checker-authoring-") as temporary:
            copy = Path(temporary) / self._dir.name
            copy.mkdir()
            for folder in self._assignment_folders():
                if folder.name == assignment_id:
                    continue
                for name in (REQUIREMENTS_FILE, RULES_FILE):
                    if (folder / name).is_file():
                        (copy / folder.name).mkdir(exist_ok=True)
                        shutil.copyfile(folder / name, copy / folder.name / name)
            _write_files(copy / assignment_id, files)
            source = YamlAssignmentSource(copy, self._course_dir)
            return source.load_course(), source.load_all()

    def parse_files(self, assignment_id: str, files: DraftFiles) -> EditedDraft:
        """See `AuthoringFiles.parse_files`."""
        problems: list[str] = []
        prefix = f"{self._dir.name}/{assignment_id}"
        requirements = _mapping(files.requirements, f"{prefix}/{REQUIREMENTS_FILE}", problems)
        rules = _mapping(files.review_rules, f"{prefix}/{RULES_FILE}", problems)
        if requirements is None or rules is None:
            raise InvalidAssignmentError("The edited files cannot be read.", tuple(problems))
        data = {
            **{key: value for key, value in requirements.items() if key != "requirements"},
            "requirements": [
                _hand_item(_body_as_text(r)) for r in _items(requirements, "requirements")
            ],
            "rules": [_hand_item(rule) for rule in _items(rules, "rules")],
            "examples": [_hand_item(example) for example in _items(rules, "examples")],
        }
        try:
            return EditedDraft.model_validate(data)
        except ValidationError as error:
            raise InvalidAssignmentError(
                "The edited files are invalid.",
                tuple(f"{prefix}{describe(item)}" for item in error.errors()),
            ) from error

    def read_files(self, assignment_id: str) -> DraftFiles | None:
        """See `AuthoringFiles.read_files`."""
        folder = self._dir / assignment_id
        paths = (folder / REQUIREMENTS_FILE, folder / RULES_FILE)
        if not all(path.is_file() for path in paths):
            return None
        requirements, rules = (_read(path, path.name) for path in paths)
        return DraftFiles(requirements=requirements, review_rules=rules)

    def write_files(self, assignment_id: str, files: DraftFiles) -> tuple[Path, ...]:
        """See `AuthoringFiles.write_files`."""
        return _write_files(self._dir / assignment_id, files)

    def remove_files(self, assignment_id: str) -> None:
        """See `AuthoringFiles.remove_files`."""
        for name in (REQUIREMENTS_FILE, RULES_FILE):
            try:
                (self._dir / assignment_id / name).unlink(missing_ok=True)
            except OSError as error:
                raise AuthoringError(f"Cannot remove {name} of {assignment_id}: {error}") from error

    def write_report(self, assignment_id: str, report: str, reply: AuthorReply) -> Path:
        """See `AuthoringFiles.write_report`."""
        if self._work_dir is None:
            raise AuthoringError("No folder is configured for the authoring reports.")
        folder = self._work_dir / assignment_id
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / REPORT_FILE).write_bytes(report.encode("utf-8"))
            (folder / DRAFT_FILE).write_bytes(reply.model_dump_json(indent=2).encode("utf-8"))
        except OSError as error:
            raise AuthoringError(
                f"Cannot write the authoring report in {folder}: {error}"
            ) from error
        return folder / REPORT_FILE

    def clone_dirs(self) -> tuple[Path, ...]:
        """See `AuthoringFiles.clone_dirs`."""
        if self._clones_dir is None or not self._clones_dir.is_dir():
            return ()
        return tuple(
            folder
            for folder in sorted(self._clones_dir.iterdir())
            if folder.is_dir() and (folder / ".git").exists()
        )

    def _assignment_folders(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return [folder for folder in sorted(self._dir.iterdir()) if folder.is_dir()]

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


def _fields(item: BaseModel, model: type[BaseModel]) -> dict[str, Any]:
    """`item`'s fields of `model` (not the author's summary and intake items), as the file
    writes them: without defaults, and `from` for a capture's source."""
    data = item.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    keys = [field.alias or name for name, field in model.model_fields.items()]
    ordered = [key for key in LEADING_KEYS if key in keys]
    ordered += [key for key in keys if key not in ordered]
    return {key: data[key] for key in ordered if key in data}


def _mapping(text: str, label: str, problems: list[str]) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        problems.append(f"{label}: not valid YAML ({error}).")
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        problems.append(f"{label}: the file must contain keys, not a list or a single value.")
        return None
    return data


def _items(data: dict[str, Any], key: str) -> list[Any]:
    items = data.get(key) or []
    return items if isinstance(items, list) else [items]


def _hand_item(item: object) -> object:
    """An item as written, with an origin and status for one added without them."""
    if not isinstance(item, dict):
        return item
    return {**{key: value for key, value in HAND_WRITTEN.items() if key not in item}, **item}


def _body_as_text(requirement: object) -> object:
    """A requirement whose probes write a body as YAML, with each body as JSON text."""
    if not isinstance(requirement, dict) or not isinstance(requirement.get("probes"), list):
        return requirement
    probes = [
        {**probe, "body": json.dumps(probe["body"], ensure_ascii=False, separators=(",", ":"))}
        if isinstance(probe, dict) and isinstance(probe.get("body"), dict | list)
        else probe
        for probe in requirement["probes"]
    ]
    return {**requirement, "probes": probes}


def _yaml_file(schema: str | None, header: str, data: dict[str, Any]) -> str:
    lines = ([SCHEMA_LINE.format(schema=schema)] if schema else []) + [header]
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return "\n".join(lines) + "\n" + body


def _write_files(folder: Path, files: DraftFiles) -> tuple[Path, ...]:
    written = (folder / REQUIREMENTS_FILE, folder / RULES_FILE)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for path, text in zip(written, (files.requirements, files.review_rules), strict=True):
            path.write_bytes(text.encode("utf-8"))
    except OSError as error:
        raise AuthoringError(f"Cannot write the files of {folder.name}: {error}") from error
    return written


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
