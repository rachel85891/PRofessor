"""`applies_when` / `skip_when`: an assignment's path is chosen per repo (stage 20)."""

import logging
from pathlib import Path

import pytest

from hw_checker.domain import AssignmentResult, NotEvaluated, NotEvaluatedReason
from hw_checker.domain.errors import FixError, InvalidAssignmentError
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.fixing import fix_target
from tests.fakes.checks import FakeRequirementChecker
from tests.unit.services.condition_data import (
    MARKER,
    UNKNOWN_SUFFIX,
    ConditionChecker,
    assignment,
    repo_with,
)

LAYERS = assignment("hw02", 2, skip_when=MARKER)
DI = assignment("hw03", 3, applies_when=MARKER)


def evaluate(repo: Path, *, checker: ConditionChecker | None = None) -> dict[str, AssignmentResult]:
    service = DeterministicCheckService(checker or ConditionChecker())
    return {a.id: service.evaluate(a, repo, None) for a in (LAYERS, DI)}


def checked(checker: ConditionChecker, assignment_id: str) -> list[str]:
    """The requirements of `assignment_id` whose checks ran."""
    return [i.requirement.id for i in checker.checked if i.assignment.id == assignment_id]


def test_applies_when_holds_so_the_requirements_are_checked(tmp_path: Path) -> None:
    checker = ConditionChecker()

    di = evaluate(repo_with(tmp_path, MARKER), checker=checker)["hw03"]

    assert di.not_checked is None and di.not_evaluated == ()
    assert checked(checker, "hw03") == ["hw03-r1", "hw03-r2"]


def test_applies_when_does_not_hold_so_nothing_is_checked(tmp_path: Path) -> None:
    checker = ConditionChecker()

    di = evaluate(repo_with(tmp_path), checker=checker)["hw03"]

    assert di.findings == ()
    assert di.not_evaluated == tuple(
        NotEvaluated(
            requirement_id=requirement_id,
            reason=NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE,
            detail=f"applies_when marker_file: no {MARKER}",
        )
        for requirement_id in ("hw03-r1", "hw03-r2")
    )
    assert checked(checker, "hw03") == []


def test_skip_when_holds_so_nothing_is_checked_and_the_detail_quotes_the_check(
    tmp_path: Path,
) -> None:
    layers = evaluate(repo_with(tmp_path, MARKER))["hw02"]

    assert layers.findings == ()
    entry = layers.not_checked
    assert entry is not None and entry.reason is NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE
    assert entry.detail == f"skip_when marker_file: {MARKER} is there"
    assert {e.requirement_id for e in layers.not_evaluated} == {"hw02-r1", "hw02-r2"}


def test_skip_when_does_not_hold_so_the_requirements_are_checked(tmp_path: Path) -> None:
    checker = ConditionChecker()

    layers = evaluate(repo_with(tmp_path), checker=checker)["hw02"]

    assert layers.not_checked is None and layers.not_evaluated == ()
    assert checked(checker, "hw02") == ["hw02-r1", "hw02-r2"]


def test_a_condition_that_cannot_be_evaluated_never_guesses_a_path(tmp_path: Path) -> None:
    results = evaluate(repo_with(tmp_path, MARKER + UNKNOWN_SUFFIX))

    for result in results.values():
        assert result.findings == ()
        assert {e.reason for e in result.not_evaluated} == {
            NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN
        }
    entry = results["hw03"].not_checked
    assert entry is not None
    assert entry.detail == (
        "applies_when marker_file could not be evaluated (environment_issue); "
        "check the repo by hand"
    )


def test_a_condition_check_that_crashes_makes_the_path_unknown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checker = FakeRequirementChecker({"hw03-applies-when": RuntimeError("boom")})

    with caplog.at_level(logging.WARNING):
        di = DeterministicCheckService(checker).evaluate(DI, repo_with(tmp_path), None)

    assert {e.reason for e in di.not_evaluated} == {NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN}
    assert "hw03-applies-when failed (RuntimeError)" in caplog.text


def test_bad_condition_data_stops_the_round(tmp_path: Path) -> None:
    checker = FakeRequirementChecker({"hw03-applies-when": InvalidAssignmentError("bad")})

    with pytest.raises(InvalidAssignmentError):
        DeterministicCheckService(checker).evaluate(DI, repo_with(tmp_path), None)


def test_the_condition_sees_the_checked_working_copy_and_the_execution(tmp_path: Path) -> None:
    checker = ConditionChecker()
    repo = repo_with(tmp_path, MARKER)

    evaluate(repo, checker=checker)

    assert [(i.requirement.id, i.repo_dir) for i in checker.conditions] == [
        ("hw02-skip-when", repo),
        ("hw03-applies-when", repo),
    ]


def test_two_repos_take_different_paths_with_nothing_shared(tmp_path: Path) -> None:
    service = DeterministicCheckService(ConditionChecker())
    di_repo = repo_with(tmp_path / "a", MARKER)
    layers_repo = repo_with(tmp_path / "b")

    first = [service.evaluate(a, di_repo, None) for a in (LAYERS, DI)]
    second = [service.evaluate(a, layers_repo, None) for a in (LAYERS, DI)]
    again = [service.evaluate(a, di_repo, None) for a in (LAYERS, DI)]

    assert [r.not_checked is None for r in first] == [False, True]
    assert [r.not_checked is None for r in second] == [True, False]
    assert again == first


GENERAL = assignment("_general", 0)
HW01 = assignment("hw01", 1)


def test_the_fix_target_is_the_highest_assignment_that_applies(tmp_path: Path) -> None:
    service = DeterministicCheckService(ConditionChecker())
    selected = (GENERAL, HW01, LAYERS, DI)

    def target(repo: Path) -> str:
        results = [service.evaluate(a, repo, None) for a in selected]
        return fix_target(selected, results).id

    assert target(repo_with(tmp_path / "di", MARKER)) == "hw03"
    assert target(repo_with(tmp_path / "layers")) == "hw02"
    # Unknown: neither path is guessed, so the review is for the last assignment that applies.
    assert target(repo_with(tmp_path / "unknown", MARKER + UNKNOWN_SUFFIX)) == "hw01"


def test_without_an_assignment_that_applies_there_is_no_fix_target() -> None:
    skipped = AssignmentResult(
        assignment_id="hw03",
        not_evaluated=(
            NotEvaluated(
                requirement_id="hw03-r1", reason=NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE
            ),
        ),
    )

    with pytest.raises(FixError, match="No selected assignment applies"):
        fix_target((DI,), (skipped,))
