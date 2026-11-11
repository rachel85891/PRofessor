"""The intake model (stage 16, design §8.3): its items, its commit choice and its schema."""

import pytest
from pydantic import ValidationError

from hw_checker.domain import Severity
from hw_checker.domain.authoring import AssignmentIntake


def _intake(**fields: object) -> AssignmentIntake:
    return AssignmentIntake.model_validate({"id": "hw02", "order": 2, "title": "T", **fields})


def test_every_item_has_a_numbered_key_under_its_heading() -> None:
    intake = _intake(
        requirements={
            "return_values": [{"text": "POST returns 201", "severity": "critical"}],
            "clean_code": [{"text": "Services end with Service"}],
            "architecture": [{"text": "Four projects"}, {"text": "API references Services"}],
        },
        lecturer_decisions={"not_to_mention": ["async"], "required_statuses": ["404"]},
        not_yet_taught=["DI"],
    )

    assert intake.items() == (
        ("requirements.return_values.1", "POST returns 201"),
        ("requirements.clean_code.1", "Services end with Service"),
        ("requirements.architecture.1", "Four projects"),
        ("requirements.architecture.2", "API references Services"),
        ("lecturer_decisions.not_to_mention.1", "async"),
        ("lecturer_decisions.required_statuses.1", "404"),
        ("not_yet_taught.1", "DI"),
    )
    assert intake.requirements.return_values[0].severity is Severity.CRITICAL


def test_the_commit_to_check_is_latest_or_an_ends_when() -> None:
    assert _intake().ends_when is None
    ends = _intake(check_commit={"ends_when": {"check": "project_count", "params": {"min": 2}}})

    assert ends.ends_when is not None
    assert ends.ends_when.check == "project_count"
    with pytest.raises(ValidationError):
        _intake(check_commit="newest")


def test_the_short_name_may_only_confirm_the_derived_one() -> None:
    assert _intake(short_name="HW2").derived_short_name == "HW2"
    with pytest.raises(ValidationError, match="short_name must be HW2"):
        _intake(short_name="Layers")


def test_unknown_keys_and_blank_items_are_refused() -> None:
    with pytest.raises(ValidationError):
        _intake(requirements={"clean_code": [{"text": " "}]})
    with pytest.raises(ValidationError):
        _intake(lecturer={})


def test_the_schema_lists_the_four_headings() -> None:
    schema = AssignmentIntake.model_json_schema()
    headings = schema["$defs"]["IntakeRequirements"]["properties"]

    assert list(headings) == ["return_values", "clean_code", "architecture", "general"]
