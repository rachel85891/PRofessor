"""YamlAssignmentSource: loads the course profile from <course_dir> and the assignments from
<assignments_dir>/<id>/, each with its review rules (ADR 0023)."""

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic_core import ErrorDetails

from hw_checker.adapters.assignments_yaml.rules import (
    COURSE_FILE,
    RULES_FILE,
    RuleLayer,
    layer_problems,
    load_profile,
    load_rule_set,
    yaml_location,
)
from hw_checker.adapters.assignments_yaml.schema import (
    AssignmentFile,
    ProbeEntry,
    RequirementEntry,
)
from hw_checker.domain import Assignment, Course, Requirement
from hw_checker.domain.errors import InvalidAssignmentError

logger = logging.getLogger(__name__)

REQUIREMENTS_FILE = "requirements.yaml"
GENERAL_ID = "_general"
GENERAL_ORDER = 0
README_HINT = "See assignments/README.md for the file format."
COURSE_ORDER = -1
"""The course's rules come before every assignment's."""

# Domain field names that differ from the key names used in the file.
_FILE_KEY_FOR_FIELD = {"http_probes": "probes"}


@dataclass(frozen=True)
class _LoadedFile:
    label: str
    assignment: Assignment
    rules_label: str


class YamlAssignmentSource:
    """Loads the course profile, `_general` and every assignment folder, validating all
    files together: the course's and the assignments' rules refer to each other.

    Any problem in any file raises `InvalidAssignmentError` listing every problem found;
    a partial result is never returned.
    """

    def __init__(self, assignments_dir: Path, course_dir: Path) -> None:
        self._dir = assignments_dir
        self._course_dir = course_dir

    def load_all(self) -> tuple[Assignment, ...]:
        """Return every assignment, including `_general`, ordered by `order`, each with its
        review rules.

        Raises:
            InvalidAssignmentError: a folder is missing, or the course or assignment data is
                invalid.
        """
        _, assignments = self._load()
        logger.info(
            "Loaded %d assignments from %s: %s",
            len(assignments),
            self._dir,
            ", ".join(assignment.id for assignment in assignments),
        )
        return assignments

    def load_course(self) -> Course:
        """Return the course profile and the course's own review rules.

        Raises:
            InvalidAssignmentError: a folder is missing, or the course or assignment data is
                invalid.
        """
        course, _ = self._load()
        return course

    def _load(self) -> tuple[Course, tuple[Assignment, ...]]:
        problems: list[str] = []
        folders = self._assignment_folders()
        if not self._course_dir.is_dir():
            raise InvalidAssignmentError(f"Course folder not found: {self._course_dir}")
        course_label = self._course_dir.name
        profile = load_profile(
            self._course_dir / COURSE_FILE, f"{course_label}/{COURSE_FILE}", problems
        )
        course_rules_label = f"{course_label}/{RULES_FILE}"
        course_rules = load_rule_set(self._course_dir / RULES_FILE, course_rules_label, problems)
        loaded = [
            result
            for folder in folders
            if (result := self._load_file(folder, problems)) is not None
        ]
        problems.extend(_cross_file_problems(loaded))
        if course_rules is not None:
            layers = [RuleLayer(course_rules_label, COURSE_ORDER, course_rules)] + [
                RuleLayer(item.rules_label, item.assignment.order, item.assignment.review_rules)
                for item in loaded
            ]
            problems.extend(layer_problems(layers, [item.assignment for item in loaded]))
        if problems or profile is None or course_rules is None:
            count = f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"
            raise InvalidAssignmentError(
                f"Invalid assignment data in {self._dir} ({count}). {README_HINT}\n"
                + "\n".join(f"- {problem}" for problem in problems)
            )
        assignments = tuple(sorted((item.assignment for item in loaded), key=lambda a: a.order))
        return Course(profile=profile, rules=course_rules), assignments

    def _assignment_folders(self) -> list[Path]:
        if not self._dir.is_dir():
            raise InvalidAssignmentError(f"Assignments folder not found: {self._dir}")
        folders: list[Path] = []
        ignored: list[str] = []
        for entry in sorted(self._dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or not (entry / REQUIREMENTS_FILE).is_file():
                ignored.append(entry.name)
            else:
                folders.append(entry)
        if ignored:
            logger.info(
                "Ignored folders in %s (hidden, or without %s): %s",
                self._dir,
                REQUIREMENTS_FILE,
                ", ".join(ignored),
            )
        return folders

    def _load_file(self, folder: Path, problems: list[str]) -> _LoadedFile | None:
        path = folder / REQUIREMENTS_FILE
        label = f"{self._dir.name}/{folder.name}/{REQUIREMENTS_FILE}"
        try:
            raw = path.read_bytes()
        except OSError as error:
            problems.append(f"{label}: cannot read the file ({error}).")
            return None
        data = _parse_yaml(raw, label, problems)
        if data is None:
            return None
        parsed = _validate_top_level(data, label, problems)
        assignment_id = folder.name if parsed is None else parsed.id
        requirements = _load_requirements(data, assignment_id, label, problems)
        if parsed is None or requirements is None:
            return None
        if not _variables_are_captured_first(requirements, label, problems):
            return None
        if parsed.id != folder.name:
            problems.append(
                f"{label}: id '{parsed.id}' does not match the folder name '{folder.name}'. "
                "Rename the folder or change the id so they match."
            )
        rules_label = f"{self._dir.name}/{folder.name}/{RULES_FILE}"
        rules = load_rule_set(folder / RULES_FILE, rules_label, problems)
        assignment = _build_assignment(
            parsed, requirements, label, hashlib.sha256(raw).hexdigest(), problems
        )
        if assignment is None or rules is None:
            return None
        return _LoadedFile(
            label=label,
            assignment=assignment.model_copy(update={"review_rules": rules}),
            rules_label=rules_label,
        )


def _variables_are_captured_first(
    requirements: list[Requirement], label: str, problems: list[str]
) -> bool:
    """Every `{name}` a probe uses must be captured by an earlier probe of the file."""
    captured: set[str] = set()
    found = False
    for requirement in requirements:
        for number, probe in enumerate(requirement.http_probes, start=1):
            for name in probe.variables_used:
                if name not in captured:
                    found = True
                    problems.append(
                        f"{label}: requirement '{requirement.id}', probe {number}: "
                        f"{{{name}}} is used before any probe captures it. Add "
                        f"'capture: {{name: {name}, from: body:id}}' to an earlier probe, "
                        "or remove the braces."
                    )
            if probe.capture is not None:
                captured.add(probe.capture.name)
    return not found


def _parse_yaml(raw: bytes, label: str, problems: list[str]) -> dict[str, Any] | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        problems.append(f"{label}: the file is not UTF-8 text. Save it with UTF-8 encoding.")
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        problems.append(f"{label}: not valid YAML ({yaml_location(error)}).")
        return None
    if not isinstance(data, dict):
        problems.append(f"{label}: the file must contain keys such as id, order and title.")
        return None
    return data


def _validate_top_level(
    data: dict[str, Any], label: str, problems: list[str]
) -> AssignmentFile | None:
    try:
        return AssignmentFile.model_validate(data)
    except ValidationError as error:
        problems.extend(f"{label}{_describe(item)}" for item in error.errors())
        return None


def _load_requirements(
    data: dict[str, Any], assignment_id: str, label: str, problems: list[str]
) -> list[Requirement] | None:
    """Validate each requirement entry on its own, so every entry's problems are reported."""
    raw_entries = data.get("requirements", [])
    if not isinstance(raw_entries, list):
        return None  # Reported by the top-level validation.
    requirements = [
        requirement
        for index, raw in enumerate(raw_entries)
        if (requirement := _load_requirement(raw, index, assignment_id, label, problems))
    ]
    return requirements if len(requirements) == len(raw_entries) else None


def _load_requirement(
    raw: object, index: int, assignment_id: str, label: str, problems: list[str]
) -> Requirement | None:
    prefix = f"{label}: requirement {_raw_requirement_id(raw, index)}"
    try:
        entry = RequirementEntry.model_validate(raw)
        return Requirement.model_validate(_requirement_data(entry, assignment_id))
    except ValidationError as error:
        problems.extend(f"{prefix}{_describe(item)}" for item in error.errors())
        return None


def _build_assignment(
    parsed: AssignmentFile,
    requirements: list[Requirement],
    label: str,
    source_hash: str,
    problems: list[str],
) -> Assignment | None:
    try:
        return Assignment.model_validate(
            {
                "id": parsed.id,
                "order": parsed.order,
                "title": parsed.title,
                "requirements": requirements,
                "taught_techniques": parsed.taught_techniques,
                "lenient_notes": parsed.lenient_notes,
                "ends_when": None
                if parsed.ends_when is None
                else {"check": parsed.ends_when.check, "params": parsed.ends_when.params},
                "source_hash": source_hash,
            }
        )
    except ValidationError as error:
        problems.extend(f"{label}{_describe(item)}" for item in error.errors())
        return None


def _requirement_data(entry: RequirementEntry, assignment_id: str) -> dict[str, Any]:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "title": entry.title,
        "description": entry.description,
        "severity": entry.severity,
        "allowed_alternatives": entry.allowed_alternatives,
        "check": entry.check,
        "params": entry.params,
        "http_probes": [_probe_data(probe, entry.id, assignment_id) for probe in entry.probes],
        "depends_on": entry.depends_on,
    }


def _probe_data(probe: ProbeEntry, requirement_id: str, assignment_id: str) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "assignment_id": assignment_id,
        "method": probe.method,
        "path": probe.path,
        "path_alternatives": probe.path_alternatives,
        "capture": None
        if probe.capture is None
        else {"name": probe.capture.name, "source": probe.capture.source},
        "expect_json": probe.expect_json,
        "expect_values": probe.expect_values,
        "body": _body_text(probe.body),
        "expected_status": probe.expected_status,
        "allowed_statuses": probe.allowed_statuses,
        "notes": probe.notes,
    }


def _body_text(body: str | dict[str, Any] | list[Any] | None) -> str | None:
    """A body written as a YAML mapping or list becomes compact JSON text."""
    if body is None or isinstance(body, str):
        return body
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def _cross_file_problems(loaded: list[_LoadedFile]) -> list[str]:
    problems: list[str] = []
    if not any(item.assignment.id == GENERAL_ID for item in loaded):
        problems.append(
            f"{GENERAL_ID} is missing: add a folder {GENERAL_ID} with a {REQUIREMENTS_FILE} "
            f"that has order: {GENERAL_ORDER}."
        )
    for item in loaded:
        order = item.assignment.order
        if item.assignment.id == GENERAL_ID and order != GENERAL_ORDER:
            problems.append(f"{item.label}: {GENERAL_ID} must have order: 0 (found {order}).")
        if item.assignment.id != GENERAL_ID and order == GENERAL_ORDER:
            problems.append(
                f"{item.label}: order must be 1 or more; order 0 is reserved for {GENERAL_ID}."
            )
    orders = _shared_across_files(loaded, lambda i: [i.assignment.order])
    for shared_order, labels in orders.items():
        problems.append(
            f"{' and '.join(labels)} use the same order {shared_order}; "
            "each assignment needs its own order."
        )
    requirement_ids = _shared_across_files(
        loaded, lambda i: [requirement.id for requirement in i.assignment.requirements]
    )
    for requirement_id, labels in requirement_ids.items():
        problems.append(
            f"{' and '.join(labels)} use the same requirement id '{requirement_id}'; "
            "requirement ids must be unique across all assignments."
        )
    problems.extend(_dependency_problems(loaded))
    return problems


def _dependency_problems(loaded: list[_LoadedFile]) -> list[str]:
    """Every `depends_on` names a known requirement, and no requirement depends on itself
    through others."""
    labels = {r.id: item.label for item in loaded for r in item.assignment.requirements}
    depends = {r.id: r.depends_on for item in loaded for r in item.assignment.requirements}
    problems = [
        f"{labels[requirement_id]}: requirement '{requirement_id}', field 'depends_on': "
        f"unknown requirement '{dependency}'; check the spelling."
        for requirement_id, dependencies in depends.items()
        for dependency in dependencies
        if dependency not in depends
    ]
    for requirement_id in depends:
        if _reaches(requirement_id, requirement_id, depends, set()):
            problems.append(
                f"{labels[requirement_id]}: requirement '{requirement_id}', field "
                "'depends_on': it depends on itself through other requirements."
            )
    return problems


def _reaches(start: str, target: str, depends: dict[str, tuple[str, ...]], seen: set[str]) -> bool:
    for dependency in depends.get(start, ()):
        if dependency == target:
            return True
        if dependency not in seen:
            seen.add(dependency)
            if _reaches(dependency, target, depends, seen):
                return True
    return False


def _shared_across_files(
    loaded: list[_LoadedFile], keys_of: Callable[[_LoadedFile], Iterable[object]]
) -> dict[object, list[str]]:
    labels_by_key: dict[object, list[str]] = defaultdict(list)
    for item in loaded:
        for key in dict.fromkeys(keys_of(item)):
            labels_by_key[key].append(item.label)
    return {key: labels for key, labels in labels_by_key.items() if len(labels) > 1}


def _raw_requirement_id(raw: object, index: int) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("id"), str):
        return f"'{raw['id']}'"
    return f"number {index + 1}"


def _describe(item: ErrorDetails) -> str:
    """Turn one validation error into ', <field>: <what is wrong>', or ': ...' without a field."""
    parts = [
        str(part + 1) if isinstance(part, int) else _FILE_KEY_FOR_FIELD.get(part, part)
        for part in item["loc"]
    ]
    field = _field_label(parts)
    if item["type"] == "extra_forbidden":
        return f", {field}: unknown key; check the spelling."
    if item["type"] == "missing":
        return f", {field}: this required field is missing."
    message = str(item["msg"]).removeprefix("Value error, ")
    return f", {field}: {message}" if field else f": {message}"


def _field_label(parts: list[str]) -> str:
    if not parts:
        return ""
    if parts[0] == "probes" and len(parts) >= 2:
        rest = ".".join(parts[2:])
        return f"probe {parts[1]}" + (f", field '{rest}'" if rest else "")
    return f"field '{'.'.join(parts)}'"
