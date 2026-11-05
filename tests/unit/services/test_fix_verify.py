"""The fixer's recheck: rebuild, rerun and recheck the fix working copy, then fall back."""

from pathlib import Path

from hw_checker.domain import (
    AssignmentResult,
    EditApplication,
    EditKind,
    ExecutionResult,
    FailureKind,
    Severity,
    StepStatus,
    VerifyResult,
)
from hw_checker.services.fixing import FixService
from tests.fakes.execution import SUCCEEDED, FakeCodeRunner
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    BREAK_FIX,
    CONTROLLER,
    DATA,
    FINDING,
    NAMING_NOTE,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

LABEL = f"{REPO.repo_id}--fix1"
NAMING_FINDING = builders.finding(
    id="f-2", requirement_id="hw01-naming", assignment_id="hw01", message_en="New name issue."
)
BROKEN = ExecutionResult(
    build_status=StepStatus.FAILED,
    run_status=StepStatus.SKIPPED,
    failure_kind=FailureKind.BUILD_FAILED,
)


def verified(setup: FixerSetup, *edits: object) -> tuple[VerifyResult, EditApplication, object]:
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    application = setup.service.apply(workspace, list(edits), ASSIGNMENT, (ASSIGNMENT,))  # type: ignore[arg-type]
    result = setup.service.verify(
        RUN_ID, REPO, workspace, (ASSIGNMENT,), SUCCEEDED, RESULTS, application, 1
    )
    return result, application, workspace


def test_a_fix_that_clears_its_requirement_and_breaks_nothing_passes(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)

    result, _, _ = verified(setup, BREAK_FIX, NAMING_NOTE)

    assert result == VerifyResult(passed=True, cleared=("hw01-update",))
    [request] = setup.runner.requests
    assert request.repo_id == LABEL
    assert request.working_copy == setup.fix_copy()
    assert request.logs_dir == setup.runs / RUN_ID / "logs" / LABEL


def test_cleared_lists_what_failed_before_and_passes_now_whatever_the_edits_claim(
    tmp_path: Path,
) -> None:
    setup = FixerSetup(tmp_path)

    result, _, _ = verified(setup, NAMING_NOTE.model_copy(update={"finding_ids": ()}))

    assert result.cleared == ("hw01-update",)


def test_a_claimed_requirement_that_still_fails_blames_that_fix(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-update": (FINDING,)})

    result, _, _ = verified(setup, NAMING_NOTE, BREAK_FIX)

    assert not result.passed
    assert result.problems == (f"the fix in {CONTROLLER} claims hw01-update, which still fails",)
    assert result.failing_fixes == (1,)


def test_a_newly_failing_requirement_blames_every_fix(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-naming": (NAMING_FINDING,)})

    result, _, _ = verified(setup, BREAK_FIX, NAMING_NOTE)

    assert result.problems == ("requirements that passed now fail: hw01-naming",)
    assert result.failing_fixes == (0,)


DEPENDENT = ASSIGNMENT.model_copy(
    update={
        "requirements": (
            ASSIGNMENT.requirements[0].model_copy(update={"depends_on": ("hw01-naming",)}),
            ASSIGNMENT.requirements[1],
        )
    }
)
"""`hw01-update` can only pass once `hw01-naming` passes."""
BOTH_FAILING = (AssignmentResult(assignment_id="hw01", findings=(FINDING, NAMING_FINDING)),)


def verified_with_dependency(
    setup: FixerSetup, results: tuple[AssignmentResult, ...], *edits: object
) -> VerifyResult:
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    application = setup.service.apply(workspace, list(edits), ASSIGNMENT, (ASSIGNMENT,))  # type: ignore[arg-type]
    return setup.service.verify(
        RUN_ID, REPO, workspace, (DEPENDENT,), SUCCEEDED, results, application, 1
    )


def test_a_claim_whose_dependency_still_fails_does_not_fail_the_recheck(tmp_path: Path) -> None:
    setup = FixerSetup(
        tmp_path, outcomes={"hw01-update": (FINDING,), "hw01-naming": (NAMING_FINDING,)}
    )

    result = verified_with_dependency(setup, BOTH_FAILING, NAMING_NOTE, BREAK_FIX)

    assert result.passed and result.failing_fixes == ()
    assert result.blocked == (
        f"`hw01-update` can't pass until `hw01-naming` passes (claimed by the fix in {CONTROLLER})",
    )


def test_a_claim_whose_dependency_passes_still_fails_the_recheck(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-update": (FINDING,)})

    result = verified_with_dependency(setup, BOTH_FAILING, NAMING_NOTE, BREAK_FIX)

    assert not result.passed and result.blocked == ()
    assert result.problems == (f"the fix in {CONTROLLER} claims hw01-update, which still fails",)
    assert result.failing_fixes == (1,)


def test_a_newly_failing_requirement_fails_the_recheck_even_with_a_blocked_claim(
    tmp_path: Path,
) -> None:
    setup = FixerSetup(
        tmp_path, outcomes={"hw01-update": (FINDING,), "hw01-naming": (NAMING_FINDING,)}
    )

    result = verified_with_dependency(setup, RESULTS, BREAK_FIX)

    assert not result.passed
    assert result.problems == ("requirements that passed now fail: hw01-naming",)
    assert len(result.blocked) == 1 and result.failing_fixes == (0,)


def test_a_broken_build_fails_the_recheck(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, runner=FakeCodeRunner(results={LABEL: BROKEN}))

    result, _, _ = verified(setup, BREAK_FIX)

    assert "the project no longer builds" in result.problems
    assert result.failing_fixes == (0,)


def test_nothing_is_rebuilt_when_nothing_was_applied(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)

    result, _, _ = verified(setup)

    assert result == VerifyResult(passed=True)
    assert setup.runner.requests == []


def test_a_failed_recheck_becomes_feedback_for_the_next_plan(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-update": (FINDING,)})
    result, application, _ = verified(setup, BREAK_FIX)

    assert FixService.feedback(application, result) == [
        f"Recheck failed: the fix in {CONTROLLER} claims hw01-update, which still fails"
    ]


def test_a_blamed_fix_falls_back_to_its_note(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-update": (FINDING,)})
    result, application, workspace = verified(setup, BREAK_FIX, NAMING_NOTE)

    fallen = setup.service.fall_back(workspace, ASSIGNMENT, (ASSIGNMENT,), application, result)  # type: ignore[arg-type]

    assert [edit.kind for edit in fallen.applied] == [EditKind.NOTE, EditKind.NOTE]
    text = setup.text(CONTROLLER)
    assert "//fix:" not in text
    assert "//Does case 1 need a break? Right now it also runs case 2." in text
    [dropped] = fallen.rejected
    assert dropped.edit == BREAK_FIX
    assert dropped.reason.endswith("kept as its fallback note")


def test_a_blamed_fix_without_a_fallback_note_is_dropped(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, outcomes={"hw01-update": (FINDING,)})
    no_note = BREAK_FIX.model_copy(update={"fallback_note": None})
    result, application, workspace = verified(setup, no_note)

    fallen = setup.service.fall_back(workspace, ASSIGNMENT, (ASSIGNMENT,), application, result)  # type: ignore[arg-type]

    assert fallen.applied == ()
    assert fallen.rejected[0].reason.endswith("dropped (no fallback note)")


def test_giving_up_says_why_there_is_no_pr() -> None:
    outcome = FixService.give_up(REPO, 2, VerifyResult(passed=False, problems=("a", "b")))

    assert outcome.proposal_id is None
    assert outcome.reason == "No PR: the recheck still failed after the fallback notes (a; b)."


CRITICAL_ASSIGNMENT = ASSIGNMENT.model_copy(
    update={
        "requirements": (
            *ASSIGNMENT.requirements,
            ASSIGNMENT.requirements[0].model_copy(
                update={"id": "hw01-read-back", "severity": Severity.CRITICAL}
            ),
        )
    }
)


def title_after_verify(
    setup: FixerSetup, results: tuple[AssignmentResult, ...], *edits: object
) -> str:
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    application = setup.service.apply(workspace, list(edits), ASSIGNMENT, (ASSIGNMENT,))  # type: ignore[arg-type]
    verification = setup.service.verify(
        RUN_ID, REPO, workspace, (CRITICAL_ASSIGNMENT,), SUCCEEDED, results, application, 1
    )
    title, _, _ = setup.service.write_text(
        RUN_ID,
        CRITICAL_ASSIGNMENT,
        (CRITICAL_ASSIGNMENT,),
        results,
        application,
        [],
        verification.cleared,
    )
    return title


def test_a_final_newline_that_clears_a_critical_requirement_is_a_real_fix(tmp_path: Path) -> None:
    read_back = builders.finding(requirement_id="hw01-read-back", assignment_id="hw01")
    results = (AssignmentResult(assignment_id="hw01", findings=(read_back,)),)
    newline = BREAK_FIX.model_copy(
        update={"file": DATA, "kind": EditKind.FINAL_NEWLINE, "find": "", "replace": ""}
    )

    title = title_after_verify(FixerSetup(tmp_path), results, newline)

    assert title == "HW1 review: fixes and questions"


def test_a_fix_whose_claimed_major_requirement_was_not_failing_is_small(tmp_path: Path) -> None:
    results = (AssignmentResult(assignment_id="hw01"),)

    assert title_after_verify(FixerSetup(tmp_path), results, BREAK_FIX) == (
        "HW1 review: small fixes and questions"
    )


def test_a_fix_that_clears_only_a_minor_requirement_is_small(tmp_path: Path) -> None:
    results = (AssignmentResult(assignment_id="hw01", findings=(NAMING_FINDING,)),)
    minor_fix = BREAK_FIX.model_copy(update={"finding_ids": ("hw01-naming",)})

    assert title_after_verify(FixerSetup(tmp_path), results, minor_fix) == (
        "HW1 review: small fixes and questions"
    )
