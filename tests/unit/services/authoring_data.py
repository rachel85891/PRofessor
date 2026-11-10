"""Synthetic data for the authoring tests (stage 16): a small course, `_general`, hw01, and
a new assignment hw02 with its intake and task text, plus canned author replies."""

from pathlib import Path
from typing import Any

import yaml

from hw_checker.domain.authoring import AuthorReply, DraftedRequirement, DraftedRule
from tests.unit.domain import builders

TASK_TEXT = (
    "# HW2: Layers\n\n"
    "Split the Users API into layers. POST /api/users returns 201 with the new user.\n"
    "Each layer is its own project.\n"
)
INTAKE: dict[str, Any] = {
    "id": "hw02",
    "order": 2,
    "title": "Layers",
    "taught": ["controllers", "layers"],
    "not_yet_taught": ["dependency injection"],
    "lecturer_decisions": {"accepted_alternatives": ["200 from POST is fine"]},
    "requirements": {
        "return_values": [{"text": "POST returns 201", "severity": "critical"}],
        "architecture": [{"text": "At least four projects"}],
    },
}
COURSE_RULES = {
    "rules": [
        {
            "id": "no-core",
            "applies_to": ["fixer"],
            "kind": "never_do",
            "text": "Never change the core.",
            "origin": {"source": "developer", "quote": "the lecturer"},
            "status": "approved",
        }
    ]
}
GENERAL = {
    "id": "_general",
    "order": 0,
    "title": "General",
    "requirements": [
        {"id": "gen-build", "kind": "build", "title": "The project builds", "severity": "critical"}
    ],
}
HW01 = {
    "id": "hw01",
    "order": 1,
    "title": "Users",
    "requirements": [
        {
            "id": "hw01-controllers",
            "kind": "structure",
            "title": "A Controllers folder exists",
            "severity": "major",
            "check": "folder_exists",
            "params": {"path": "Controllers"},
        }
    ],
}


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False)
    path.write_text(text, encoding="utf-8")


def make_folders(root: Path, intake: dict[str, Any] | None = None) -> tuple[Path, Path]:
    """Write the synthetic course and assignments under `root`; return both folders."""
    course, assignments = root / "course", root / "assignments"
    write(course / "course.yaml", builders.course_profile().model_dump(mode="json"))
    write(course / "review_rules.yaml", COURSE_RULES)
    write(assignments / "_general" / "requirements.yaml", GENERAL)
    write(assignments / "hw01" / "requirements.yaml", HW01)
    write(assignments / "hw02" / "intake.yaml", intake or INTAKE)
    write(assignments / "hw02" / "task.md", TASK_TEXT)
    return assignments, course


def post_requirement(**overrides: Any) -> DraftedRequirement:
    data: dict[str, Any] = {
        "id": "hw02-post",
        "kind": "http",
        "title": "POST /api/users creates a user",
        "severity": "critical",
        "allowed_alternatives": ["200 from POST is fine"],
        "probes": [
            {
                "method": "POST",
                "path": "/api/users",
                "body": '{"userName": "newuser"}',
                "expected_status": 201,
                "allowed_statuses": [201, 200],
            }
        ],
        "origin": {"source": "task_text", "quote": "POST /api/users returns 201"},
        "status": "draft",
        "summary": "POST /api/users answers 201 (or 200).",
        "intake_items": [
            "requirements.return_values.1",
            "lecturer_decisions.accepted_alternatives.1",
        ],
    }
    return DraftedRequirement.model_validate({**data, **overrides})


def projects_requirement(**overrides: Any) -> DraftedRequirement:
    data: dict[str, Any] = {
        "id": "hw02-projects",
        "kind": "structure",
        "title": "The solution has four projects",
        "severity": "major",
        "check": "project_count",
        "params": {"min": 4},
        "origin": {
            "source": "intake",
            "item": "requirements.architecture.1",
            "quote": "At least four projects",
        },
        "status": "draft",
        "summary": "At least four .csproj projects.",
        "intake_items": ["requirements.architecture.1"],
    }
    return DraftedRequirement.model_validate({**data, **overrides})


def not_yet_taught_rule(**overrides: Any) -> DraftedRule:
    data: dict[str, Any] = {
        "id": "hw02-not-yet-taught",
        "applies_to": ["fixer", "judge"],
        "kind": "never_mention",
        "text": "Do not mention dependency injection (taught later).",
        "until": "hw02",
        "origin": {"source": "inferred"},
        "status": "draft",
        "summary": "The fixer never mentions dependency injection in HW2.",
        "intake_items": ["not_yet_taught.1"],
    }
    return DraftedRule.model_validate({**data, **overrides})


def valid_reply() -> AuthorReply:
    """A draft that covers every item of `INTAKE` and passes validation."""
    return AuthorReply(
        requirements=(post_requirement(), projects_requirement()),
        rules=(not_yet_taught_rule(),),
    )
