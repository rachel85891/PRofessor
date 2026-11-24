"""`until` on requirements and lenient notes: what a round of each assignment sees."""

from pathlib import Path

import pytest

from hw_checker.domain import (
    Assignment,
    ExecutionResult,
    LenientNote,
    Requirement,
    StepStatus,
    assignments_in_effect,
)
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.checks import FakeCheckCatalog, FakeRequirementChecker
from tests.unit.domain import builders


def req(requirement_id: str, until: str | None = None) -> Requirement:
    return builders.requirement(id=requirement_id, until=until)


def assignment(
    assignment_id: str,
    order: int,
    *requirements: Requirement,
    notes: tuple[str, ...] = (),
    ends: tuple[LenientNote, ...] = (),
) -> Assignment:
    return Assignment(
        id=assignment_id,
        order=order,
        title=assignment_id,
        requirements=requirements,
        lenient_notes=notes,
        lenient_note_ends=ends,
    )


GENERAL = assignment(
    "_general",
    0,
    req("gen-r1", until="hw02"),
    req("gen-r2"),
    notes=("Text files are fine", "Keep going"),
    ends=(LenientNote(text="Text files are fine", until="hw02"),),
)
HW01 = assignment("hw01", 1, req("hw01-r1", until="hw01"), req("hw01-r2", until="hw03"))
HW02 = assignment("hw02", 2, req("hw02-r1"))
HW03 = assignment("hw03", 3, req("hw03-r1", until="hw03"))
TREE = (GENERAL, HW01, HW02, HW03)


def selected(assignment_id: str) -> AssignmentCatalogService:
    return AssignmentCatalogService(FakeAssignmentSource(TREE), FakeCheckCatalog([]))


def requirement_ids(assignment_id: str) -> list[str]:
    return [
        r.id
        for a in selected(assignment_id).select_for_round(assignment_id)
        for r in a.requirements
    ]


def notes_of(assignment_id: str) -> tuple[str, ...]:
    return selected(assignment_id).select_for_round(assignment_id)[0].lenient_notes


def test_a_requirement_is_in_the_rounds_up_to_its_until_and_gone_after() -> None:
    assert requirement_ids("hw01") == ["gen-r1", "gen-r2", "hw01-r1", "hw01-r2"]
    assert requirement_ids("hw02") == ["gen-r1", "gen-r2", "hw01-r2", "hw02-r1"]
    assert requirement_ids("hw03") == ["gen-r2", "hw01-r2", "hw02-r1", "hw03-r1"]


def test_a_note_ended_by_an_assignment_is_gone_from_later_rounds() -> None:
    assert notes_of("hw02") == ("Text files are fine", "Keep going")
    assert notes_of("hw03") == ("Keep going",)


def test_the_checked_assignments_own_until_items_stay() -> None:
    assert "hw03-r1" in requirement_ids("hw03")
    assert "hw01-r1" in requirement_ids("hw01")


def test_a_round_of_the_first_assignment_keeps_every_general_item() -> None:
    assert notes_of("hw01") == ("Text files are fine", "Keep going")
    assert {"gen-r1", "gen-r2"} <= set(requirement_ids("hw01"))


def test_an_item_without_until_never_ends() -> None:
    assert all("gen-r2" in requirement_ids(a) for a in ("hw01", "hw02", "hw03"))


def test_the_end_dates_stay_on_the_loaded_assignments() -> None:
    assert TREE[0].lenient_note_ends[0].until == "hw02"
    assert assignments_in_effect(TREE, HW03)[0].lenient_note_ends == ()
    assert assignments_in_effect(TREE, HW02)[0].lenient_note_ends[0].until == "hw02"


def test_a_note_end_must_name_one_of_the_notes() -> None:
    with pytest.raises(ValueError, match="lenient_note_ends"):
        assignment("_general", 0, ends=(LenientNote(text="Not a note", until="hw01"),))


def test_the_source_hash_is_not_touched() -> None:
    hashed = HW01.model_copy(update={"source_hash": "a" * 64})

    [_, kept] = assignments_in_effect((GENERAL, hashed), hashed)

    assert kept.source_hash == "a" * 64


def evaluated_in_hw01(round_of: str, tmp_path: Path) -> set[str]:
    """The requirements of hw01 the deterministic checks report on in a round of `round_of`."""
    hw01 = next(a for a in selected(round_of).select_for_round(round_of) if a.id == "hw01")
    execution = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)
    result = DeterministicCheckService(FakeRequirementChecker()).evaluate(hw01, tmp_path, execution)
    return {e.requirement_id for e in result.not_evaluated}


def test_an_ended_requirement_is_absent_from_what_a_round_evaluates(tmp_path: Path) -> None:
    """The checks get the requirements the round hands them, so an ended requirement is
    neither passed, failed nor not evaluated."""
    assert evaluated_in_hw01("hw01", tmp_path) == {"hw01-r1", "hw01-r2"}
    assert evaluated_in_hw01("hw02", tmp_path) == {"hw01-r2"}
