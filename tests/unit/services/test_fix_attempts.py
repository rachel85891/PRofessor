"""Which fixer attempt a proposal is made of (`best_attempt`), and the repo report's
sentence about it (`attempt_choice`)."""

from hw_checker.domain import EditApplication, FixAttempt, FixPlan, VerifyResult
from hw_checker.services.fixing import attempt_choice, best_attempt
from tests.unit.services.fixer_data import NAMING_NOTE

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
