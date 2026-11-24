"""The text-file items end at HW04 (developer decision 2026-11-24): every round up to HW04
sees them, a round of a later assignment (made up here, in memory) does not."""

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import Assignment, assignments_in_effect
from hw_checker.domain.course import in_effect

SOURCE = YamlAssignmentSource(REPO_ROOT / "assignments", REPO_ROOT / "course")
REQUIREMENTS = {"hw01-data-file-in-repo", "hw02-file-access-in-repositories"}
RULES = {"no-file-exists", "file-access-note"}
EXAMPLES = {"get-by-id-put-break-newline"}
LATER = Assignment(id="hw05", order=5, title="A later assignment", requirements=())


def _round(checked: Assignment) -> tuple[set[str], list[str], set[str]]:
    """The requirement ids, the lenient notes and the rule and example ids of a round."""
    course = SOURCE.load_course()
    assignments = (*SOURCE.load_all(), LATER)
    selected = assignments_in_effect(assignments, checked)
    passed = {a.id: a.order < checked.order for a in assignments}
    rules = in_effect([course.rules.rules, *(a.review_rules.rules for a in selected)], passed)
    examples = in_effect(
        [course.rules.examples, *(a.review_rules.examples for a in selected)], passed
    )
    return (
        {r.id for a in selected for r in a.requirements},
        [note for a in selected for note in a.lenient_notes],
        {item.id for item in (*rules, *examples)},
    )


def _ending_note() -> str:
    ending = [
        note.text for a in SOURCE.load_all() for note in a.lenient_note_ends if note.until == "hw04"
    ]
    assert len(ending) == 1
    return ending[0]


def test_every_round_up_to_hw04_sees_the_text_file_items() -> None:
    hw04 = next(a for a in SOURCE.load_all() if a.id == "hw04")

    requirements, notes, items = _round(hw04)

    assert requirements >= REQUIREMENTS
    assert _ending_note() in notes
    assert items >= (RULES | EXAMPLES)


def test_a_round_after_hw04_does_not_see_them() -> None:
    requirements, notes, items = _round(LATER)

    assert not REQUIREMENTS & requirements
    assert _ending_note() not in notes
    assert not (RULES | EXAMPLES) & items
