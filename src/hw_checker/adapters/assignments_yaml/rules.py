"""Loading the course profile and the `review_rules.yaml` files (ADR 0023, design §8.1).

Like the requirements, every file is validated against its domain model, and every
problem in every file is collected, so one run lists them all.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from hw_checker.domain import Assignment, CourseProfile, FixExample, ReviewRule, ReviewRuleSet
from hw_checker.domain.course import RULE_REFERENCE, PolicyItem

COURSE_FILE = "course.yaml"
RULES_FILE = "review_rules.yaml"
_RULE_SET_KEYS = frozenset(ReviewRuleSet.model_fields)
_ITEM_FIELDS = ("replaces", "after")


@dataclass(frozen=True)
class RuleLayer:
    """The rules and examples of one file, and where it stands in the inheritance: the
    course's file first (`order` -1), then the assignments' by `order`."""

    label: str
    order: int
    rules: ReviewRuleSet


def load_profile(path: Path, label: str, problems: list[str]) -> CourseProfile | None:
    """`course.yaml` as a `CourseProfile`; None (with the problems) when it is invalid."""
    data = read_mapping(path, label, problems, required=True)
    if data is None:
        return None
    try:
        return CourseProfile.model_validate(data)
    except ValidationError as error:
        problems.extend(f"{label}{describe(item)}" for item in error.errors())
        return None


def load_rule_set(path: Path, label: str, problems: list[str]) -> ReviewRuleSet | None:
    """A `review_rules.yaml`, each rule and example validated on its own so every entry's
    problems are reported; an absent file is an empty set."""
    if not path.is_file():
        return ReviewRuleSet()
    data = read_mapping(path, label, problems, required=False)
    if data is None:
        return None
    unknown = sorted(set(data) - _RULE_SET_KEYS)
    problems.extend(f"{label}, field '{key}': unknown key; check the spelling." for key in unknown)
    rules = _entries(data, "rules", ReviewRule, "rule", label, problems)
    examples = _entries(data, "examples", FixExample, "example", label, problems)
    if unknown or rules is None or examples is None:
        return None
    return ReviewRuleSet(rules=tuple(rules), examples=tuple(examples))


def layer_problems(layers: Sequence[RuleLayer], assignments: Sequence[Assignment]) -> list[str]:
    """What is wrong across the files: a duplicate id, an `until` that names no assignment,
    a `replaces` or `after` that names no earlier item, and a `{rule:<id>}` that names no
    rule."""
    problems: list[str] = []
    assignment_ids = {assignment.id for assignment in assignments}
    rule_ids = {rule.id for layer in layers for rule in layer.rules.rules}
    for kind, items_of in (
        ("rule", lambda layer: layer.rules.rules),
        ("example", lambda layer: layer.rules.examples),
    ):
        problems += _item_problems(layers, kind, items_of, assignment_ids)
    for layer in layers:
        for rule in layer.rules.rules:
            problems += [
                f"{layer.label}: rule '{rule.id}': {{rule:{target}}} names an unknown rule; "
                "check the spelling."
                for text in rule.texts
                for target in RULE_REFERENCE.findall(text)
                if target not in rule_ids
            ]
    return problems


def read_mapping(
    path: Path, label: str, problems: list[str], *, required: bool
) -> dict[str, Any] | None:
    """The YAML mapping in `path`; None (with the problem) when it cannot be read."""
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        if required:
            problems.append(f"{label}: the file is missing.")
        return None
    except OSError as error:
        problems.append(f"{label}: cannot read the file ({error}).")
        return None
    except UnicodeDecodeError:
        problems.append(f"{label}: the file is not UTF-8 text. Save it with UTF-8 encoding.")
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        problems.append(f"{label}: not valid YAML ({yaml_location(error)}).")
        return None
    if data is None and not required:
        return {}
    if not isinstance(data, dict):
        problems.append(f"{label}: the file must contain keys, not a list or a single value.")
        return None
    return data


def describe(item: ErrorDetails) -> str:
    """One validation error as ', field '<field>': <what is wrong>'."""
    field = ".".join(str(part + 1) if isinstance(part, int) else part for part in item["loc"])
    where = f", field '{field}'" if field else ""
    if item["type"] == "extra_forbidden":
        return f"{where}: unknown key; check the spelling."
    if item["type"] == "missing":
        return f"{where}: this required field is missing."
    return f"{where}: {str(item['msg']).removeprefix('Value error, ')}"


def _entries[ModelT: BaseModel](
    data: dict[str, Any],
    key: str,
    model: type[ModelT],
    kind: str,
    label: str,
    problems: list[str],
) -> list[ModelT] | None:
    raw_entries = data.get(key) or []
    if not isinstance(raw_entries, list):
        problems.append(f"{label}, field '{key}': must be a list.")
        return None
    entries: list[ModelT] = []
    for index, raw in enumerate(raw_entries):
        name = (
            f"'{raw['id']}'"
            if isinstance(raw, dict) and isinstance(raw.get("id"), str)
            else f"number {index + 1}"
        )
        try:
            entries.append(model.model_validate(raw))
        except ValidationError as error:
            problems.extend(f"{label}: {kind} {name}{describe(item)}" for item in error.errors())
    return entries if len(entries) == len(raw_entries) else None


def _item_problems(
    layers: Sequence[RuleLayer],
    kind: str,
    items_of: Callable[[RuleLayer], Sequence[PolicyItem]],
    assignment_ids: set[str],
) -> list[str]:
    problems: list[str] = []
    seen: dict[str, str] = {}
    for layer in sorted(layers, key=lambda layer: layer.order):
        for item in items_of(layer):
            where = f"{layer.label}: {kind} '{item.id}'"
            if item.id in seen:
                problems.append(
                    f"{where}: the id is used again (first in {seen[item.id]}); "
                    f"{kind} ids must be unique across the course and all assignments."
                )
            for field in _ITEM_FIELDS:
                target = getattr(item, field)
                if target is not None and target not in seen:
                    problems.append(
                        f"{where}, field '{field}': no earlier {kind} '{target}' (the course's, "
                        "an earlier assignment's, or earlier in this file); check the spelling."
                    )
            if item.until is not None and item.until not in assignment_ids:
                problems.append(
                    f"{where}, field 'until': unknown assignment '{item.until}'; "
                    "check the spelling."
                )
            seen.setdefault(item.id, layer.label)
    return problems


def yaml_location(error: yaml.YAMLError) -> str:
    """Where in the file a YAML error is, and what it is."""
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    if mark is None:
        return str(problem)
    return f"line {mark.line + 1}, column {mark.column + 1}: {problem}"


SCHEMA_FILES: dict[str, type[BaseModel]] = {
    "course.schema.json": CourseProfile,
    "review_rules.schema.json": ReviewRuleSet,
}
"""The JSON Schema of each file, by its name in `course/schemas/` (ADR 0023)."""


def schema_text(model: type[BaseModel]) -> str:
    """`model`'s JSON Schema as committed: generated from the model, never written by hand."""
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"
