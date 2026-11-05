"""The policy each role sees: rule inheritance across assignments (a synthetic hw02), roles,
placeholders and examples (ADR 0023)."""

from pathlib import Path

from hw_checker.domain import Assignment, ReviewRole, ReviewRuleSet, RuleExample
from hw_checker.services.review_policy import role_policy
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    COURSE,
    HW01_RULES,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

PUT_TEXT = "PUT returns `NoContent()` from hw02 on: the assignment asks for it."
ASYNC_TEXT = "Async actions are taught in hw02: a synchronous file read may get a note."
HW01_ONLY_TEXT = "Only in hw01: do not comment on the routes."
HW01 = ASSIGNMENT.model_copy(
    update={
        "review_rules": HW01_RULES.model_copy(
            update={
                "rules": (
                    *HW01_RULES.rules,
                    builders.review_rule(id="hw01-routes", text=HW01_ONLY_TEXT, until="hw01"),
                )
            }
        )
    }
)
HW02 = Assignment(
    id="hw02",
    order=2,
    title="Products API",
    requirements=(),
    review_rules=ReviewRuleSet(
        rules=(
            builders.review_rule(
                id="put-returns-no-content",
                kind="note_only",
                text=PUT_TEXT,
                replaces="put-returns-ok",
            ),
            builders.review_rule(id="async-note", kind="note_only", text=ASYNC_TEXT),
        )
    ),
)


def test_hw02_sees_the_replaced_and_added_rules_and_not_hw01s_until_rules() -> None:
    hw01 = role_policy(COURSE, HW01, (HW01,), ReviewRole.FIXER).rules
    hw02 = role_policy(COURSE, HW02, (HW01, HW02), ReviewRole.FIXER).rules

    assert HW01_ONLY_TEXT in hw01
    assert HW01_ONLY_TEXT not in hw02
    put = next(index for index, text in enumerate(hw01) if text.startswith("PUT returns `Ok()`"))
    assert hw02[put] == PUT_TEXT
    assert not any(text.startswith("PUT returns `Ok()`") for text in hw02)
    assert hw02[-1] == ASYNC_TEXT


def test_hw02s_fixer_prompt_has_the_replaced_and_added_rules(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, "hw02", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, HW02, (HW01, HW02), RESULTS, 1)

    assert f". {PUT_TEXT}\n" in prompt
    assert f". {ASYNC_TEXT}\n" in prompt
    assert HW01_ONLY_TEXT not in prompt
    assert "PUT returns `Ok()`" not in prompt.split("# The edits you may plan")[0]


def test_each_role_sees_only_its_rules() -> None:
    writer_only = builders.review_rule(
        id="writer-only", text="Keep it short.", applies_to=["writer"]
    )
    hw = HW02.model_copy(update={"review_rules": ReviewRuleSet(rules=(writer_only,))})

    assert role_policy(COURSE, hw, (hw,), ReviewRole.WRITER).rules == ("Keep it short.",)
    assert "Keep it short." not in role_policy(COURSE, hw, (hw,), ReviewRole.FIXER).rules


def test_placeholders_are_filled_for_the_role() -> None:
    course = builders.course(
        rules=(
            builders.review_rule(id="first", text="First, about {short_name}."),
            builders.review_rule(
                id="second",
                text="Taught{taught_so_far}.",
                clarification="Rule {rule:first} comes before rule {rule:second}.",
            ),
        )
    )
    hw = HW02.model_copy(update={"review_rules": ReviewRuleSet(), "taught_techniques": ("LINQ",)})

    policy = role_policy(course, hw, (hw,), ReviewRole.JUDGE)

    assert policy.rules == ("First, about HW2.", "Taught (taught so far: LINQ).")
    assert policy.clarifications == ("Rule 1 comes before rule 2.",)
    untaught = hw.model_copy(update={"taught_techniques": ()})
    assert role_policy(course, untaught, (untaught,), ReviewRole.JUDGE).rules[1] == "Taught."


def test_a_rules_own_example_is_shown_after_the_examples(tmp_path: Path) -> None:
    example = RuleExample(file="A.cs", find="x;", replace="//fix: y\ny;")
    rule = builders.review_rule(id="with-example", example=example)
    hw = HW02.model_copy(update={"review_rules": ReviewRuleSet(rules=(rule,))})
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, "hw02", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, hw, (HW01, hw), RESULTS, 1)

    number = role_policy(COURSE, hw, (HW01, hw), ReviewRole.FIXER).rules.index(rule.text) + 1
    assert f"Example 3. An edit that follows rule {number}.\n\n```json\n" in prompt
    assert '"find": "x;"' in prompt
