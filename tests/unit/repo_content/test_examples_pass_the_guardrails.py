"""Every `fix` an example in effect shows is a safe fix the code accepts (2026-11-19).

The code refuses a `fix` no safe-fix detector in effect recognizes, so an example that
shows one would teach the fixer an edit it loses an attempt on. Each assignment's
examples in effect (the course's and those of assignments 1..N, the rules' own examples
included) go through the code's safe-fix check (`safe_fix_problem`) with that
assignment's safe fixes, on a file holding their own `find`.
"""

import json
from collections.abc import Sequence

import pytest

from hw_checker.domain import (
    Assignment,
    Course,
    EditKind,
    FixExample,
    PlannedEdit,
    ReviewRole,
    ReviewRuleSet,
)
from hw_checker.domain.course import in_effect
from hw_checker.services.fix_edits import safe_fix_problem
from hw_checker.services.fix_review import Allowances, repo_code
from hw_checker.services.review_policy import role_policy
from tests.unit.services.fixer_data import COURSE, SOURCE

ASSIGNMENTS = SOURCE.load_all()
CHECKED = ("hw01", "hw02", "hw03", "hw04")
EXAMPLE_REASON = "An example the prompts show."


def _passed(assignment: Assignment, assignments: Sequence[Assignment]) -> dict[str, bool]:
    return {a.id: a.order < assignment.order for a in assignments}


def _shown_fixes(
    course: Course, assignment: Assignment, assignments: Sequence[Assignment]
) -> list[tuple[str, PlannedEdit, str]]:
    """The `fix` edits of the examples in effect for `assignment`: (item id, edit, the
    situation the example describes)."""
    earlier = [a for a in assignments if a.order <= assignment.order]
    passed = _passed(assignment, assignments)
    fixes: list[tuple[str, PlannedEdit, str]] = []
    layers = [course.rules.examples, *(a.review_rules.examples for a in earlier)]
    examples = in_effect(layers, passed)
    for example in examples:
        if ReviewRole.FIXER in example.applies_to:
            fixes += [(example.id, edit, example.situation) for edit in _plan_fixes(example)]
    rules = in_effect([course.rules.rules, *(a.review_rules.rules for a in earlier)], passed)
    for rule in rules:
        shown = rule.example
        if ReviewRole.FIXER not in rule.applies_to or shown is None:
            continue
        style = course.profile.comment_style.get("." + shown.file.rsplit(".", 1)[-1])
        if style is not None and style.fix_marker in shown.replace:
            edit = PlannedEdit(
                file=shown.file,
                find=shown.find,
                replace=shown.replace,
                kind=EditKind.FIX,
                finding_ids=(),
                reason=EXAMPLE_REASON,
                fallback_note=None,
                note_section=None,
            )
            fixes.append((rule.id, edit, rule.text))
    return fixes


def _plan_fixes(example: FixExample) -> list[PlannedEdit]:
    edits = json.loads(example.plan)["edits"]
    return [PlannedEdit.model_validate(edit) for edit in edits if edit["kind"] == "fix"]


def refused_fixes(
    course: Course, assignment: Assignment, assignments: Sequence[Assignment]
) -> list[str]:
    """Each `fix` of an example in effect for `assignment` that the guardrails refuse, as
    `item id: reason`."""
    shown = _shown_fixes(course, assignment, assignments)
    files = [edit.file for _, edit, _ in shown]
    found = [r.id for a in assignments for r in a.requirements]
    policy = role_policy(course, assignment, assignments, ReviewRole.FIXER, files, found)
    refused: list[str] = []
    for item_id, edit, situation in shown:
        allowances = Allowances(
            detectors=policy.detectors,
            approved_notes=policy.approved_notes,
            styles=course.profile.comment_style,
            # The example's situation says what the checks' findings name.
            findings={item: (situation,) for item in found},
            code=repo_code({edit.file: edit.find.encode("utf-8")}),
        )
        problem = safe_fix_problem(edit, allowances, edit.replace)
        refused += [f"{item_id}: {problem}"] if problem else []
    return refused


@pytest.mark.parametrize("assignment_id", CHECKED)
def test_every_fix_an_example_in_effect_shows_is_a_safe_fix(assignment_id: str) -> None:
    assignment = next(a for a in ASSIGNMENTS if a.id == assignment_id)

    assert refused_fixes(COURSE, assignment, ASSIGNMENTS) == []


def test_hw01s_client_fix_example_is_in_effect_through_hw03_only() -> None:
    def shown(assignment_id: str) -> set[str]:
        assignment = next(a for a in ASSIGNMENTS if a.id == assignment_id)
        return {item for item, _, _ in _shown_fixes(COURSE, assignment, ASSIGNMENTS)}

    assert "client-id-condition-await" in shown("hw03")
    assert "client-id-condition-await" not in shown("hw04")


def test_an_example_with_a_refused_fix_is_caught() -> None:
    real_bug = FixExample.model_validate(
        {
            "id": "real-bug-fixed",
            "applies_to": ["fixer", "judge"],
            "situation": "`Services/ProductService.cs` saves the stock in `Price`.",
            "plan": json.dumps(
                {
                    "edits": [
                        {
                            "file": "Services/ProductService.cs",
                            "find": "        existing.Price = product.Stock;",
                            "replace": "        //fix: Save the stock in Stock\n"
                            "        existing.Stock = product.Stock;",
                            "kind": "fix",
                            "finding_ids": [],
                            "reason": "The stock overwrites the price.",
                            "fallback_note": "Which property should the stock be saved in?",
                            "note_section": None,
                        }
                    ],
                    "files_notes": [],
                }
            ),
            "origin": {"source": "developer", "quote": "an invented example"},
            "status": "approved",
        }
    )
    rules = COURSE.rules
    course = COURSE.model_copy(
        update={"rules": ReviewRuleSet(rules=rules.rules, examples=(*rules.examples, real_bug))}
    )
    (hw01,) = (a for a in ASSIGNMENTS if a.id == "hw01")

    assert refused_fixes(course, hw01, ASSIGNMENTS) == [
        "real-bug-fixed: this change is not one of the safe fixes the rules allow; plan a "
        "note instead"
    ]
