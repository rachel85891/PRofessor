"""The policy each role sees: rule inheritance across assignments (a synthetic hw02), roles,
placeholders and examples (ADR 0023)."""

from pathlib import Path

from hw_checker.domain import Assignment, ReviewRole, ReviewRuleSet, RuleExample
from hw_checker.services.review_policy import role_policy
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    CONTROLLER,
    COURSE,
    HW01_RULES,
    NAMING_NOTE,
    REPO,
    RESULTS,
    RUN_ID,
    SOURCE,
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
    workspace = setup.service.prepare(RUN_ID, (HW01, HW02), (), REPO, setup.fetched)

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

    # The course's writer rules come first; the assignment's follow them.
    assert role_policy(COURSE, hw, (hw,), ReviewRole.WRITER).rules[-1] == "Keep it short."
    assert "Keep it short." not in role_policy(COURSE, hw, (hw,), ReviewRole.FIXER).rules


def test_the_full_round_review_rules_reach_their_roles() -> None:
    by_id = {rule.id: rule.text for rule in COURSE.rules.rules}
    # Up to its `{rule:safe-fixes}` placeholder, which is filled with a number.
    unrequested = by_id["only-what-is-needed"].split("{rule:")[0]
    writer = (by_id["no-comment-pointers"], by_id["naming-items"])
    hw = HW02.model_copy(update={"review_rules": ReviewRuleSet()})

    def rules(role: ReviewRole) -> tuple[str, ...]:
        return role_policy(COURSE, hw, (hw,), role, (CONTROLLER,)).rules

    for role in (ReviewRole.FIXER, ReviewRole.JUDGE):
        assert any(text.startswith(unrequested) for text in rules(role))
        assert not set(writer) & set(rules(role))
    assert set(writer) <= set(rules(ReviewRole.WRITER))
    assert not any(text.startswith(unrequested) for text in rules(ReviewRole.WRITER))
    forbidden = role_policy(COURSE, hw, (hw,), ReviewRole.WRITER, (CONTROLLER,)).forbidden
    assert [scoped.item.message for scoped in forbidden] == [
        'never write "A comment is there to guide you" or "A comment asks..."; the section\'s '
        "heading already says the code is marked"
    ]


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
    workspace = setup.service.prepare(RUN_ID, (HW01, HW02), (), REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, hw, (HW01, hw), RESULTS, 1)

    policy = role_policy(COURSE, hw, (HW01, hw), ReviewRole.FIXER, (CONTROLLER,))
    number = policy.rules.index(rule.text) + 1
    # The course's own rule examples come first; this one follows them.
    assert f"Example 6. An edit that follows rule {number}.\n\n```json\n" in prompt
    assert '"find": "x;"' in prompt


NOT_YET_TAUGHT = "Do not mention GET all (not required in HW1), layers, or a `Models` folder"


def test_hw01s_not_yet_taught_rule_stops_after_hw01(tmp_path: Path) -> None:
    rule = next(rule for rule in HW01_RULES.rules if rule.id == "not-yet-taught")
    layers = HW02.model_copy(update={"review_rules": ReviewRuleSet()})
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (HW01, layers), (), REPO, setup.fetched)

    hw01_prompt = setup.service.plan_prompt(workspace, HW01, (HW01,), RESULTS, 1)
    hw02_prompt = setup.service.plan_prompt(workspace, layers, (HW01, layers), RESULTS, 1)

    assert rule.until == "hw01"
    assert NOT_YET_TAUGHT in hw01_prompt
    assert NOT_YET_TAUGHT not in hw02_prompt
    judge = role_policy(COURSE, layers, (HW01, layers), ReviewRole.JUDGE).rules
    assert not any(NOT_YET_TAUGHT in text for text in judge)


STORED_NAME_NOTE = (
    "The note on a stored property says, in this order: the name to use (PascalCase); that "
    "the data file stores the current names; that if the student renames them, they set "
    "`PropertyNameCaseInsensitive = true` in the `JsonSerializerOptions`"
)


def test_the_fixer_and_judge_prompts_show_the_naming_rules_stored_property_note(
    tmp_path: Path,
) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (HW01,), (), REPO, setup.fetched)
    plan = setup.service.plan_prompt(workspace, HW01, (HW01,), RESULTS, 1)
    application = setup.service.apply(workspace, [NAMING_NOTE], HW01, (HW01,))
    setup.service.self_review(RUN_ID, workspace, HW01, (HW01,), application)
    [judge] = setup.llm.calls

    for prompt in (plan, judge.prompt):
        assert STORED_NAME_NOTE in prompt
        assert "the `PropertyNameCaseInsensitive` tip instead" not in prompt
        # The rule's own example: the rename made safe by the option, never instead of it.
        assert "if you rename them, set PropertyNameCaseInsensitive = true" in prompt


def test_no_example_in_effect_names_a_data_file() -> None:
    catalog = SOURCE.load_all()
    hw04 = next(assignment for assignment in catalog if assignment.id == "hw04")
    naming = next(rule for rule in COURSE.rules.rules if rule.id == "naming")

    assert "Name the data file only as the student's code names it" in naming.text
    files = ("Models/Product.cs", "wwwroot/index.html", "wwwroot/app.js", "README.md")
    for role in (ReviewRole.FIXER, ReviewRole.JUDGE, ReviewRole.WRITER):
        examples = role_policy(COURSE, hw04, catalog, role, files).examples
        assert all("products.json" not in example.plan for example in examples)
    fixer_examples = role_policy(COURSE, hw04, catalog, ReviewRole.FIXER, files).examples
    assert any("The data file stores the current names" in ex.plan for ex in fixer_examples)


CONNECTION_STRING = (
    "Never ask or tell the student to move the connection string to `appsettings.json`"
)


def test_the_connection_string_rule_reaches_the_fixer_judge_and_writer_prompts(
    tmp_path: Path,
) -> None:
    rule = next(rule for rule in COURSE.rules.rules if rule.id == "connection-string")
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)

    plan = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)

    assert rule.until is None and rule.replaces is None
    assert rule.origin.quote == "developer decision 2026-11-19 (HW04 round review)"
    assert CONNECTION_STRING in plan
    for role in (ReviewRole.FIXER, ReviewRole.JUDGE, ReviewRole.WRITER):
        rules = role_policy(COURSE, ASSIGNMENT, (ASSIGNMENT,), role).rules
        assert any(text.startswith(CONNECTION_STRING) for text in rules)


ONLY_SAFE_FIXES = "A `fix` is only one of the safe fixes the rules in effect allow"
STOCK_NOTE = "//Which property should the stock be saved in?"


def test_the_fixer_and_judge_prompts_show_a_real_bug_as_a_note(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)

    plan = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)
    judge = role_policy(COURSE, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.JUDGE)

    assert ONLY_SAFE_FIXES in plan
    assert STOCK_NOTE in plan
    assert "existing.Stock = product.Stock" not in plan
    assert any(text.startswith(ONLY_SAFE_FIXES) for text in judge.rules)
    assert any(STOCK_NOTE in example.plan for example in judge.examples)


NO_ENTITY_FRAMEWORK = "Entity Framework is taught in HW05. Never note, ask about or fix EF code"


def test_the_entity_framework_rule_is_in_effect_for_hw04_for_the_fixer_judge_and_writer(
    tmp_path: Path,
) -> None:
    rule = next(rule for rule in COURSE.rules.rules if rule.id == "no-entity-framework")
    catalog = SOURCE.load_all()
    hw04 = next(assignment for assignment in catalog if assignment.id == "hw04")
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)

    plan = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)

    assert rule.until is None and rule.replaces is None
    assert rule.origin.quote == "developer decision 2026-11-22 (HW04 pilot review)"
    assert NO_ENTITY_FRAMEWORK in plan
    assert "the note only says which line doesn't compile" in plan
    for role in (ReviewRole.FIXER, ReviewRole.JUDGE, ReviewRole.WRITER):
        rules = role_policy(COURSE, hw04, catalog, role).rules
        assert any(text.startswith(NO_ENTITY_FRAMEWORK) for text in rules)


NO_PROBE_VALUES = "Never quote a value the checker sent in a request"


def test_the_probe_values_rule_is_in_effect_for_hw04_for_the_fixer_judge_and_writer(
    tmp_path: Path,
) -> None:
    rule = next(rule for rule in COURSE.rules.rules if rule.id == "no-probe-values")
    catalog = SOURCE.load_all()
    hw04 = next(assignment for assignment in catalog if assignment.id == "hw04")
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)

    plan = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)

    assert rule.until is None and rule.replaces is None
    assert rule.origin.quote == "developer decision 2026-11-23 (HW04 round review)"
    assert NO_PROBE_VALUES in plan
    assert '"PUT with a strong password returns 500"' in plan
    for role in (ReviewRole.FIXER, ReviewRole.JUDGE, ReviewRole.WRITER):
        rules = role_policy(COURSE, hw04, catalog, role).rules
        assert any(text.startswith(NO_PROBE_VALUES) for text in rules)
