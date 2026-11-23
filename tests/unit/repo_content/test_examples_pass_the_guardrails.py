"""Every `fix` an example in effect shows is a fix the code accepts (2026-11-19).

The code refuses a `fix` no safe-fix detector in effect recognizes, and one without a fix
marker line directly above the changed code, so an example that shows one would teach the
fixer an edit it loses an attempt on. Each assignment's examples in effect (the course's
and those of assignments 1..N, the rules' own examples included) go through every
guardrail of the code (`apply_edits`, the safe-fix check included) with that assignment's
rules and safe fixes, on a file holding their own `find` (2026-11-23: the marker check).
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
from hw_checker.services.fix_edits import EditRules, apply_edits
from hw_checker.services.fix_review import Allowances, repo_code
from hw_checker.services.review_policy import role_policy
from tests.unit.services.fixer_data import COURSE, SOURCE, config

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
    `item id: reason`, with the fixer's limits."""
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
        rules = EditRules(
            max_edits=config().max_edits,
            max_fix_lines=config().max_fix_lines,
            styles=course.profile.comment_style,
            forbidden=policy.forbidden,
            forbidden_changes=policy.forbidden_changes,
            allowances=allowances,
        )
        source = {edit.file: edit.find.encode("utf-8")}
        rejected = apply_edits(source, [edit], rules).application.rejected
        refused += [f"{item_id}: {rejection.reason}" for rejection in rejected]
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


def _with_example(
    item_id: str, situation: str, file: str, find: str, replace: str, fallback: str
) -> Course:
    """The course with one more invented example showing one `fix`."""
    edit: dict[str, object] = {
        "file": file,
        "find": find,
        "replace": replace,
        "kind": "fix",
        "finding_ids": [],
        "reason": "An invented example's fix.",
        "fallback_note": fallback,
        "note_section": None,
    }
    example = FixExample.model_validate(
        {
            "id": item_id,
            "applies_to": ["fixer", "judge"],
            "situation": situation,
            "plan": json.dumps({"edits": [edit], "files_notes": []}),
            "origin": {"source": "developer", "quote": "an invented example"},
            "status": "approved",
        }
    )
    rules = COURSE.rules
    return COURSE.model_copy(
        update={"rules": ReviewRuleSet(rules=rules.rules, examples=(*rules.examples, example))}
    )


def test_an_example_with_a_refused_fix_is_caught() -> None:
    course = _with_example(
        "real-bug-fixed",
        "`Services/ProductService.cs` saves the stock in `Price`.",
        "Services/ProductService.cs",
        "        existing.Price = product.Stock;",
        "        //fix: Save the stock in Stock\n        existing.Stock = product.Stock;",
        "Which property should the stock be saved in?",
    )
    (hw01,) = (a for a in ASSIGNMENTS if a.id == "hw01")

    assert refused_fixes(course, hw01, ASSIGNMENTS) == [
        "real-bug-fixed: this change is not one of the safe fixes the rules allow; plan a "
        "note instead"
    ]


def test_an_example_with_code_above_its_fix_marker_is_caught() -> None:
    # The hw01 break example's shape before 2026-11-23: the added `{` comes before the
    # `//fix:` line, so the fix-marker guardrail refuses it.
    course = _with_example(
        "break-with-new-braces",
        "`Controllers/OrdersController.cs` keeps reading the file after it found the order.",
        "Controllers/OrdersController.cs",
        "                    if (order.Id == id)\n                        found = line;",
        "                    if (order.Id == id)\n                    {\n"
        "                        found = line;\n                        //fix: Found- break\n"
        "                        break;\n                    }",
        "Once the order is found, does the loop need to keep reading the file?",
    )
    (hw01,) = (a for a in ASSIGNMENTS if a.id == "hw01")

    assert refused_fixes(course, hw01, ASSIGNMENTS) == [
        "break-with-new-braces: a fix needs a //fix: ... line directly above the changed code"
    ]
