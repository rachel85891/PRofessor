"""The fields a `code` requirement holds for the code reviewer (stage 16, for stage 8b)."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.domain import RequirementKind
from tests.unit.domain import builders
from tests.unit.services.authoring_data import make_folders, write


def test_a_code_requirement_carries_what_the_reviewer_needs() -> None:
    requirement = builders.requirement(
        kind=RequirementKind.CODE,
        allowed_alternatives=("Returning 204 is fine",),
        reviewer_guidance="Look at GET by id: a missing user returns NotFound.",
        lenient_notes=("async is not required yet",),
        concerns={"files": ["**/Controllers/*.cs"], "roles": ["api"]},
    )

    assert requirement.concerns is not None
    assert requirement.concerns.files == ("**/Controllers/*.cs",)
    assert requirement.concerns.roles == ("api",)


@pytest.mark.parametrize(
    "field",
    [
        {"reviewer_guidance": "Look"},
        {"lenient_notes": ("lenient",)},
        {"concerns": {"files": ["*.cs"]}},
    ],
)
def test_only_a_code_requirement_has_the_reviewer_fields(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="only a code requirement has them"):
        builders.requirement(kind=RequirementKind.BUILD, **field)


def test_concerns_name_files_or_roles() -> None:
    with pytest.raises(ValidationError, match="give files, roles or both"):
        builders.requirement(kind=RequirementKind.CODE, concerns={})


def test_the_loader_reads_the_reviewer_fields(tmp_path: Path) -> None:
    assignments, course = make_folders(tmp_path)
    hw01 = assignments / "hw01" / "requirements.yaml"
    data = yaml.safe_load(hw01.read_text(encoding="utf-8"))
    data["requirements"].append(
        {
            "id": "hw01-not-found",
            "kind": "code",
            "title": "GET by id returns NotFound for a missing user",
            "severity": "major",
            "reviewer_guidance": "A missing user is NotFound, not 200 with null.",
            "lenient_notes": ["NoContent is not expected"],
            "concerns": {"roles": ["api"]},
        }
    )
    write(hw01, data)

    loaded = YamlAssignmentSource(assignments, course).load_all()

    requirement = next(a for a in loaded if a.id == "hw01").requirements[-1]
    assert requirement.reviewer_guidance.startswith("A missing user")
    assert requirement.lenient_notes == ("NoContent is not expected",)
    assert requirement.concerns is not None and requirement.concerns.roles == ("api",)
