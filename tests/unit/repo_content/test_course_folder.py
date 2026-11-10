"""The committed course/ folder (ADR 0023): it loads, every item is approved with its
origin, the JSON Schemas are the generated ones, and no course policy is left in code."""

import re
from pathlib import Path

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.adapters.assignments_yaml.rules import SCHEMA_FILES, schema_text
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import ItemStatus

SOURCE = YamlAssignmentSource(REPO_ROOT / "assignments", REPO_ROOT / "course")
PACKAGE = REPO_ROOT / "src" / "hw_checker"
MOVED = (
    "second-year",
    "ASP.NET Core Web API homework",
    "File.Exists",
    "BadRequest(",
    "Hi! ",
    "Fixed in this PR",
    "For you to do (marked in the code)",
    "Names (C# conventions)",
    "fixes and questions",
    "The assignment asks",
    "PropertyNameCaseInsensitive",
    "Why isn't it implemented",
    # Stage 18: the client file types and the C#-only text left in 3f.
    "<!--fix:",
    "camelCase",
    "onclick",
    "C# conventions",
    "{ get; set; }",
)
"""Policy moved to course/ and assignments/hw01/ in stages 3f and 18."""
DESCRIBING = {
    ("domain/course.py", "Hi! "),
    ("domain/course.py", "second-year"),
    ("domain/course.py", "<!--fix:"),
}
"""Docstrings that only describe the data format with an example of its value."""


def test_the_course_loads_and_every_item_is_approved_with_its_origin() -> None:
    sets = [SOURCE.load_course().rules] + [a.review_rules for a in SOURCE.load_all()]
    items = [item for rules in sets for item in (*rules.rules, *rules.examples)]

    assert len(items) == 25
    assert all(item.status is ItemStatus.APPROVED and item.origin.quote for item in items)


def test_no_frame_override_is_committed() -> None:
    assert not (REPO_ROOT / "course" / "prompts").exists()


@pytest.mark.parametrize("name", sorted(SCHEMA_FILES))
def test_the_committed_json_schemas_are_generated_from_the_models(name: str) -> None:
    committed = (REPO_ROOT / "course" / "schemas" / name).read_bytes().decode("utf-8")

    assert committed == schema_text(SCHEMA_FILES[name]), (
        f"Regenerate course/schemas/{name} with schema_text() in "
        "hw_checker.adapters.assignments_yaml.rules"
    )


def _policy_files() -> list[Path]:
    frames = sorted((PACKAGE / "prompts").glob("*.j2"))
    code = sorted((PACKAGE / "services").glob("*.py"))
    return [*frames, *code, PACKAGE / "domain" / "fixes.py", PACKAGE / "domain" / "course.py"]


@pytest.mark.parametrize("path", _policy_files(), ids=lambda path: path.name)
def test_no_moved_policy_is_left_in_frames_or_code(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(PACKAGE).as_posix()

    found = [
        words
        for words in MOVED
        if re.search(re.escape(words), text, re.IGNORECASE) and (relative, words) not in DESCRIBING
    ]

    assert found == [], f"{relative} still holds course policy: {found}"
