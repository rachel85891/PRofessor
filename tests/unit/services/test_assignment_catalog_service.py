from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry, check
from hw_checker.domain import (
    Assignment,
    CheckCondition,
    CheckInput,
    Finding,
    Requirement,
    RequirementKind,
    ReviewRuleSet,
    Severity,
    TaskEnd,
)
from hw_checker.domain.errors import InvalidAssignmentError, NotFoundError
from hw_checker.services.assignments import AssignmentCatalogService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.checks import FakeCheckCatalog
from tests.unit.domain import builders


def structure(requirement_id: str, check_name: str, **params: Any) -> Requirement:
    return Requirement(
        id=requirement_id,
        title=f"Requirement {requirement_id}",
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check=check_name,
        params=params,
    )


def code(requirement_id: str) -> Requirement:
    return Requirement(
        id=requirement_id, title="Code", severity=Severity.MINOR, kind=RequirementKind.CODE
    )


def assignment(assignment_id: str, order: int, *requirements: Requirement) -> Assignment:
    return Assignment(id=assignment_id, order=order, title=assignment_id, requirements=requirements)


GENERAL = assignment("_general", 0, code("gen-r1"))
HW01 = assignment("hw01", 1, structure("hw01-r1", "file_exists", glob="*.cs"))
HW02 = assignment("hw02", 2, code("hw02-r1"))
HW03 = assignment("hw03", 3, structure("hw03-r1", "folder_exists", path="Controllers"))
TREE = (GENERAL, HW01, HW02, HW03)
KNOWN_CHECKS = FakeCheckCatalog(["file_exists", "folder_exists"])


def service(
    assignments: tuple[Assignment, ...] = TREE, catalog: FakeCheckCatalog = KNOWN_CHECKS
) -> AssignmentCatalogService:
    return AssignmentCatalogService(FakeAssignmentSource(assignments), catalog)


def ids(assignments: tuple[Assignment, ...]) -> list[str]:
    return [a.id for a in assignments]


@pytest.mark.parametrize(
    ("assignment_id", "expected"),
    [
        ("hw01", ["_general", "hw01"]),
        ("hw02", ["_general", "hw01", "hw02"]),
        ("hw03", ["_general", "hw01", "hw02", "hw03"]),
    ],
)
def test_selects_general_and_assignments_up_to_the_selected_one(
    assignment_id: str, expected: list[str]
) -> None:
    assert ids(service().select_for_round(assignment_id)) == expected


def test_selection_is_sorted_with_general_first_whatever_the_source_order() -> None:
    selected = service((HW03, HW01, GENERAL, HW02)).select_for_round("hw02")

    assert ids(selected) == ["_general", "hw01", "hw02"]


def test_list_assignments_returns_everything_from_the_source() -> None:
    assert service().list_assignments() == TREE


@pytest.mark.parametrize("assignment_id", ["hw09", "_general"])
def test_unknown_id_or_general_raises_not_found_with_valid_ids(assignment_id: str) -> None:
    with pytest.raises(NotFoundError) as raised:
        service().select_for_round(assignment_id)

    message = str(raised.value)
    assert f"Unknown assignment '{assignment_id}'" in message
    assert "hw01, hw02, hw03" in message
    assert "_general," not in message


def test_unknown_check_and_invalid_params_raise_one_error_with_all_problems() -> None:
    hw01 = assignment(
        "hw01",
        1,
        structure("hw01-r1", "file_exits", glob="*.cs"),
        structure("hw01-r2", "file_exists", globb="*.cs"),
    )
    catalog = FakeCheckCatalog(
        ["file_exists", "folder_exists"], {"file_exists": ("param 'globb': unknown param",)}
    )

    with pytest.raises(InvalidAssignmentError) as raised:
        service((GENERAL, hw01), catalog).select_for_round("hw01")

    message = str(raised.value)
    assert "(2 problems)" in message
    assert "- hw01: requirement 'hw01-r1', check 'file_exits': unknown check" in message
    assert "- hw01: requirement 'hw01-r2', check 'file_exists': param 'globb'" in message


def test_problem_in_a_later_assignment_does_not_block_selection() -> None:
    later = assignment("hw03", 3, structure("hw03-r1", "not_written_yet"))
    catalog_service = service((GENERAL, HW01, HW02, later))

    assert ids(catalog_service.select_for_round("hw02")) == ["_general", "hw01", "hw02"]
    assert catalog_service.validate_all() == (
        "hw03: requirement 'hw03-r1', check 'not_written_yet': unknown check 'not_written_yet'",
    )


def test_validate_all_is_empty_for_valid_data() -> None:
    assert service().validate_all() == ()


def test_only_requirements_with_checks_are_validated() -> None:
    catalog = FakeCheckCatalog(["file_exists", "folder_exists"])

    service(catalog=catalog).select_for_round("hw02")

    assert catalog.validated == [("file_exists", {"glob": "*.cs"})]


def test_source_errors_propagate_unchanged() -> None:
    error = InvalidAssignmentError("broken requirements.yaml")
    failing = AssignmentCatalogService(FakeAssignmentSource(error=error), KNOWN_CHECKS)

    for call in (failing.list_assignments, failing.validate_all):
        with pytest.raises(InvalidAssignmentError) as raised:
            call()
        assert raised.value is error
    with pytest.raises(InvalidAssignmentError) as raised:
        failing.select_for_round("hw01")
    assert raised.value is error


class GlobParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    glob: str


class PathParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str


@check("file_exists", GlobParams, "A file matching the glob exists.")
def dummy_file_exists(check_input: CheckInput, params: GlobParams) -> tuple[Finding, ...]:
    return ()


@check("folder_exists", PathParams, "A folder exists.")
def dummy_folder_exists(check_input: CheckInput, params: PathParams) -> tuple[Finding, ...]:
    return ()


def test_works_with_a_real_registry() -> None:
    registry = CheckRegistry([dummy_file_exists, dummy_folder_exists])
    broken = assignment("hw02", 2, structure("hw02-r1", "folder_exists", path=3))
    real_service = AssignmentCatalogService(FakeAssignmentSource((GENERAL, HW01, broken)), registry)

    assert ids(real_service.select_for_round("hw01")) == ["_general", "hw01"]
    with pytest.raises(
        InvalidAssignmentError, match="hw02-r1', check 'folder_exists': param 'path'"
    ):
        real_service.select_for_round("hw02")


def with_rules(base: Assignment, *rules: Any) -> Assignment:
    return base.model_copy(update={"review_rules": ReviewRuleSet(rules=rules)})


def test_a_round_refuses_to_start_while_a_selected_rule_is_a_draft() -> None:
    draft = builders.review_rule(id="hw01-draft", status="draft")
    course = builders.course(rules=(builders.review_rule(id="course-draft", status="draft"),))
    tree = (GENERAL, with_rules(HW01, draft), HW02, HW03)
    catalog = AssignmentCatalogService(FakeAssignmentSource(tree, course=course), KNOWN_CHECKS)

    with pytest.raises(InvalidAssignmentError, match="drafts; approve them first") as raised:
        catalog.select_for_round("hw02")

    assert "- course: rule 'course-draft'" in str(raised.value)
    assert "- hw01: rule 'hw01-draft'" in str(raised.value)


def test_a_draft_of_a_later_assignment_does_not_block_the_round() -> None:
    later = with_rules(HW03, builders.review_rule(id="hw03-draft", status="draft"))
    catalog = AssignmentCatalogService(
        FakeAssignmentSource((GENERAL, HW01, HW02, later)), KNOWN_CHECKS
    )

    assert [a.id for a in catalog.select_for_round("hw02")] == ["_general", "hw01", "hw02"]


def with_end(base: Assignment, check_name: str, **params: Any) -> Assignment:
    return base.model_copy(update={"ends_when": TaskEnd(check=check_name, params=params)})


def test_ends_when_must_name_a_known_file_list_check_with_valid_params() -> None:
    registry = CheckRegistry(builtin_checks())
    valid = with_end(HW01, "project_count", min=2)
    tree = (GENERAL, valid)

    assert AssignmentCatalogService(FakeAssignmentSource(tree), registry).validate_all() == ()
    for broken, expected in [
        (with_end(HW01, "no_such_check"), "hw01: ends_when, check 'no_such_check': unknown"),
        (with_end(HW01, "project_count"), "param 'min': this required param is missing"),
        (with_end(HW01, "controller_suffix"), "this check reads file contents"),
    ]:
        catalog = AssignmentCatalogService(FakeAssignmentSource((GENERAL, broken)), registry)
        problems = catalog.validate_all()
        assert len(problems) == 1
        assert expected in problems[0]


def with_condition(base: Assignment, field: str, check_name: str, **params: Any) -> Assignment:
    return base.model_copy(update={field: CheckCondition(check=check_name, params=params)})


def test_a_condition_must_name_a_known_check_with_valid_params() -> None:
    registry = CheckRegistry(builtin_checks())
    # Unlike ends_when, a condition may read file contents (controller_suffix does).
    valid = with_condition(HW01, "applies_when", "controller_suffix")

    assert (
        AssignmentCatalogService(FakeAssignmentSource((GENERAL, valid)), registry).validate_all()
        == ()
    )
    for broken, expected in [
        (
            with_condition(HW01, "applies_when", "no_such_check"),
            "hw01: applies_when, check 'no_such_check': unknown",
        ),
        (
            with_condition(HW01, "skip_when", "project_count"),
            "hw01: skip_when, check 'project_count': param 'min': this required param is missing",
        ),
        (
            with_condition(HW01, "skip_when", "project_count", min=2, max=3),
            "param 'max': unknown param",
        ),
    ]:
        catalog = AssignmentCatalogService(FakeAssignmentSource((GENERAL, broken)), registry)
        problems = catalog.validate_all()
        assert len(problems) == 1
        assert expected in problems[0]


def test_a_round_with_an_invalid_condition_does_not_start() -> None:
    broken = with_condition(HW01, "skip_when", "no_such_check")
    catalog = AssignmentCatalogService(
        FakeAssignmentSource((GENERAL, broken)), CheckRegistry(builtin_checks())
    )

    with pytest.raises(InvalidAssignmentError, match="skip_when, check 'no_such_check'"):
        catalog.select_for_round("hw01")


def test_an_assignment_cannot_have_both_conditions() -> None:
    condition = CheckCondition(check="project_count", params={"min": 2})

    with pytest.raises(ValueError, match="applies_when or skip_when, not both"):
        Assignment.model_validate(
            HW01.model_dump() | {"applies_when": condition, "skip_when": condition}
        )
