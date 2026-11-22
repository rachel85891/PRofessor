"""Which fixer attempt a proposal is made of (`best_attempt`), the repo report's sentence
about it (`attempt_choice`) and the attempts' rejected edits (`rejected_lines`)."""

from hw_checker.domain import EditApplication, FixAttempt, FixPlan, RejectedEdit, VerifyResult
from hw_checker.services.fixing import (
    MAX_REJECTED_REASON_CHARS,
    FixService,
    attempt_choice,
    best_attempt,
    rejected_lines,
)
from tests.unit.services.fixer_data import BREAK_FIX, CONTROLLER, NAMING_NOTE, REPO

HW04_FINDINGS = ("hw04-register-weak", "hw04-update-weak", "hw01-read-back")


def attempt(
    number: int, edits: int, uncovered: tuple[str, ...] = (), passed: bool = True
) -> FixAttempt:
    applied = (NAMING_NOTE,) * edits
    return FixAttempt(
        number=number,
        plan=FixPlan(edits=applied, files_notes=()),
        application=EditApplication(applied=applied),
        verification=VerifyResult(
            passed=passed,
            problems=() if passed else ("the project no longer builds",),
            uncovered=uncovered,
        ),
    )


def test_the_attempt_that_leaves_fewer_requirements_unasked_beats_one_with_more_edits() -> None:
    more_edits = attempt(1, edits=6, uncovered=HW04_FINDINGS)
    covering = attempt(2, edits=4)

    assert best_attempt([more_edits, covering]) == covering


def test_an_earlier_covering_attempt_beats_a_later_one_with_more_edits() -> None:
    covering = attempt(1, edits=4)
    more_edits = attempt(2, edits=6, uncovered=HW04_FINDINGS)

    assert best_attempt([covering, more_edits]) == covering


def test_with_equal_coverage_the_attempt_with_more_edits_is_kept() -> None:
    first = attempt(1, edits=6, uncovered=("hw04-register-weak",))
    second = attempt(2, edits=4, uncovered=("hw04-update-weak",))

    assert best_attempt([first, second]) == first


def test_on_a_full_tie_the_later_attempt_is_kept() -> None:
    first = attempt(1, edits=4)
    second = attempt(2, edits=4)

    assert best_attempt([first, second]) == second


def test_an_attempt_whose_recheck_failed_is_never_kept_even_with_full_coverage() -> None:
    failed = attempt(2, edits=6, passed=False)
    passing = attempt(1, edits=1, uncovered=HW04_FINDINGS)

    assert best_attempt([passing, failed]) == passing
    assert best_attempt([failed]) is None


def test_without_findings_of_the_reviewed_assignment_the_choice_is_by_applied_edits() -> None:
    assert best_attempt([attempt(1, edits=6), attempt(2, edits=4)]) == attempt(1, edits=6)
    assert best_attempt([]) is None


def test_the_sentence_names_the_fewest_unasked_as_the_reason() -> None:
    tried = [attempt(1, edits=6, uncovered=HW04_FINDINGS), attempt(2, edits=4)]

    assert attempt_choice(tried, fell_back=False) == (
        "Attempt 2 of 2 is used: its recheck passed with 4 applied edits (the fewest failing "
        "requirements left without an edit of the attempts that passed; attempt 1: recheck "
        "passed, 6 applied edits, 3 failing requirements left without an edit)."
    )


def test_the_sentence_names_the_chosen_attempts_unasked_count() -> None:
    tried = [attempt(1, edits=1, uncovered=HW04_FINDINGS), attempt(2, edits=6, passed=False)]

    assert attempt_choice(tried, fell_back=False) == (
        "Attempt 1 of 2 is used: its recheck passed with 1 applied edit, leaving 3 failing "
        "requirements without an edit (the most of the attempts that passed; attempt 2: "
        "recheck failed)."
    )


def test_the_sentence_names_both_reasons_when_both_decided() -> None:
    tried = [
        attempt(1, edits=6, uncovered=("hw04-register-weak",)),
        attempt(2, edits=2),
        attempt(3, edits=4),
    ]

    assert attempt_choice(tried, fell_back=False) == (
        "Attempt 3 of 3 is used: its recheck passed with 4 applied edits (the fewest failing "
        "requirements left without an edit, then the most applied edits, of the attempts "
        "that passed; attempt 1: recheck passed, 6 applied edits, 1 failing requirement left "
        "without an edit; attempt 2: recheck passed, 2 applied edits)."
    )


def test_without_unasked_requirements_the_sentence_is_unchanged() -> None:
    tried = [attempt(1, edits=4), attempt(2, edits=1)]

    assert attempt_choice(tried, fell_back=False) == (
        "Attempt 1 of 2 is used: its recheck passed with 4 applied edits (the most of the "
        "attempts that passed; attempt 2: recheck passed, 1 applied edit)."
    )


# ------------------------------------------------- the rejected edits in the repo report


def rejecting(number: int, *rejected: RejectedEdit) -> FixAttempt:
    return attempt(number, edits=1).model_copy(
        update={"application": EditApplication(applied=(NAMING_NOTE,), rejected=rejected)}
    )


def test_each_attempts_rejected_edits_are_listed_with_their_reasons() -> None:
    claiming = BREAK_FIX.model_copy(update={"finding_ids": ("hw04-register-weak", "hw01-read")})
    tried = [
        rejecting(1, RejectedEdit(edit=claiming, reason="find occurs 0 times in A.cs")),
        rejecting(2, RejectedEdit(edit=NAMING_NOTE, reason="a note may only add comments")),
    ]

    assert rejected_lines(tried) == (
        f"Attempt 1: fix in {CONTROLLER} (claims `hw04-register-weak`, `hw01-read`): "
        "find occurs 0 times in A.cs",
        f"Attempt 2: note in {CONTROLLER} (claims nothing): a note may only add comments",
    )


def test_a_rejected_edits_line_holds_no_text_of_the_edit() -> None:
    [line] = rejected_lines([rejecting(1, RejectedEdit(edit=BREAK_FIX, reason="not safe"))])

    assert BREAK_FIX.find.strip() not in line and "break;" not in line


def test_a_long_reason_is_cut_and_its_whitespace_collapsed() -> None:
    reason = "word  \n " + "x" * 400
    [line] = rejected_lines([rejecting(1, RejectedEdit(edit=BREAK_FIX, reason=reason))])

    shown = line.split("): ", 1)[1]
    assert len(shown) == MAX_REJECTED_REASON_CHARS
    assert shown.startswith("word x") and shown.endswith("…")


def test_no_rejected_edits_give_no_lines() -> None:
    assert rejected_lines([attempt(1, edits=2), attempt(2, edits=1)]) == ()


def test_no_proposal_still_reports_the_rejected_edits() -> None:
    failed = VerifyResult(passed=False, problems=("the project no longer builds",))

    outcome = FixService.give_up(REPO, 2, failed, ("Attempt 1: fix in A.cs (claims nothing): x",))

    assert outcome.rejected_edits == ("Attempt 1: fix in A.cs (claims nothing): x",)
