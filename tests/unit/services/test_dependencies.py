"""Holding back the requirements whose `depends_on` names one that did not pass."""

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    NotEvaluated,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
)
from hw_checker.services.dependencies import hold_back_dependents
from tests.unit.domain import builders


def requirement(
    requirement_id: str, *depends_on: str, kind: RequirementKind = RequirementKind.HTTP
) -> Requirement:
    extra: dict[str, object] = {}
    if kind is RequirementKind.HTTP:
        assignment_id = requirement_id.split("-")[0]
        extra["http_probes"] = (
            builders.probe(requirement_id=requirement_id, assignment_id=assignment_id),
        )
    if kind is RequirementKind.STRUCTURE:
        extra = {"check": "file_exists", "params": {"glob": "*.cs"}}
    return builders.requirement(id=requirement_id, kind=kind, depends_on=depends_on, **extra)


HW01 = Assignment(
    id="hw01",
    order=1,
    title="Users",
    requirements=(requirement("hw01-create"), requirement("hw01-list")),
)
HW04 = Assignment(
    id="hw04",
    order=4,
    title="Passwords",
    requirements=(
        requirement("hw04-register", "hw01-create"),
        requirement("hw04-update", "hw04-register"),
        requirement("hw04-strength"),
    ),
)


def failed(requirement_id: str) -> AssignmentResult:
    return AssignmentResult(
        assignment_id=requirement_id.split("-")[0],
        findings=(
            builders.finding(
                id=f"{requirement_id}-f",
                requirement_id=requirement_id,
                assignment_id=requirement_id.split("-")[0],
            ),
        ),
    )


def held_back(requirement_id: str, needed: str, reason: NotEvaluatedReason) -> NotEvaluated:
    return NotEvaluated(requirement_id=requirement_id, reason=reason, detail=needed)


def test_a_dependent_in_another_assignment_of_a_failing_requirement_is_held_back() -> None:
    hw04 = failed("hw04-register")

    [hw01, adjusted] = hold_back_dependents((HW01, HW04), (failed("hw01-create"), hw04))

    assert hw01 == failed("hw01-create")
    assert adjusted.findings == ()
    assert adjusted.not_evaluated == (
        held_back("hw04-register", "`hw01-create`", NotEvaluatedReason.DEPENDENCY_FAILED),
        # A chain: it needs the held-back registration, so it is held back too.
        held_back("hw04-update", "`hw04-register`", NotEvaluatedReason.DEPENDENCY_FAILED),
    )


def test_a_passing_dependent_is_held_back_too() -> None:
    [_, adjusted] = hold_back_dependents(
        (HW01, HW04), (failed("hw01-create"), AssignmentResult(assignment_id="hw04"))
    )

    assert [entry.requirement_id for entry in adjusted.not_evaluated] == [
        "hw04-register",
        "hw04-update",
    ]


def test_with_its_dependency_passing_a_requirement_is_untouched() -> None:
    results = (AssignmentResult(assignment_id="hw01"), failed("hw04-register"))

    assert hold_back_dependents((HW01, HW04), results) == (
        results[0],
        AssignmentResult(
            assignment_id="hw04",
            findings=failed("hw04-register").findings,
            not_evaluated=(
                held_back("hw04-update", "`hw04-register`", NotEvaluatedReason.DEPENDENCY_FAILED),
            ),
        ),
    )


def test_an_independent_requirement_is_untouched() -> None:
    hw04 = failed("hw04-strength")

    [_, adjusted] = hold_back_dependents((HW01, HW04), (failed("hw01-create"), hw04))

    assert adjusted.findings == hw04.findings


def test_a_dependent_of_an_unreachable_database_gets_its_reason_and_detail() -> None:
    hw01 = AssignmentResult(
        assignment_id="hw01",
        not_evaluated=(
            held_back("hw01-create", "SQL Server", NotEvaluatedReason.DATABASE_UNAVAILABLE),
        ),
    )

    [_, adjusted] = hold_back_dependents((HW01, HW04), (hw01, failed("hw04-register")))

    assert adjusted.findings == ()
    assert adjusted.not_evaluated == (
        held_back("hw04-register", "SQL Server", NotEvaluatedReason.DATABASE_UNAVAILABLE),
        held_back("hw04-update", "SQL Server", NotEvaluatedReason.DATABASE_UNAVAILABLE),
    )


def test_an_already_not_evaluated_dependent_keeps_its_reason() -> None:
    route = NotEvaluated(
        requirement_id="hw04-register",
        reason=NotEvaluatedReason.ROUTE_NOT_FOUND,
        detail="routes: /api/users",
    )
    unsent = NotEvaluated(requirement_id="hw04-update", reason=NotEvaluatedReason.DEPENDENCY_FAILED)
    hw04 = AssignmentResult(assignment_id="hw04", not_evaluated=(route, unsent))

    [_, adjusted] = hold_back_dependents((HW01, HW04), (failed("hw01-create"), hw04))

    assert adjusted.not_evaluated == (
        route,
        # An unsent probe's entry names, now, the requirement it needs.
        held_back("hw04-update", "`hw04-register`", NotEvaluatedReason.DEPENDENCY_FAILED),
    )


def test_a_structure_requirement_the_check_service_did_not_run_stays_as_it_was() -> None:
    layers = requirement("hw02-layers", kind=RequirementKind.STRUCTURE)
    references = requirement("hw02-references", "hw02-layers", kind=RequirementKind.STRUCTURE)
    hw02 = Assignment(id="hw02", order=2, title="Layers", requirements=(layers, references))
    result = AssignmentResult(
        assignment_id="hw02",
        findings=failed("hw02-layers").findings,
        not_evaluated=(
            held_back("hw02-references", "`hw02-layers`", NotEvaluatedReason.DEPENDENCY_FAILED),
        ),
    )

    assert hold_back_dependents((hw02,), (result,)) == (result,)


def test_a_dependency_of_an_assignment_without_a_result_does_not_count() -> None:
    hw04 = AssignmentResult(assignment_id="hw04")

    assert hold_back_dependents((HW01, HW04), (hw04,)) == (hw04,)
