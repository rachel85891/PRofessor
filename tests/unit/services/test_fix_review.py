"""Which self-review violations are acted on: quoted rules, and edits never a violation."""

from typing import Any

import pytest

from hw_checker.domain import EditKind, NoteSection, PlannedEdit, ReviewViolation, SelfReview
from hw_checker.services.fix_review import judge, never_a_violation, numbered_rules

RULES = numbered_rules(
    "1. Write every comment you add in English, short.\n"
    "3. Never implement the core of the assignment (for example an unimplemented GET by id).\n"
    "4. Never add `File.Exists` checks returning `BadRequest`, and do not comment on them.\n"
    "9. Second-year level: no refactoring, and no techniques that have not been taught.\n"
)


def fix(find: str, replace: str) -> PlannedEdit:
    return PlannedEdit(
        file="Controllers/UsersController.cs",
        find=find,
        replace=replace,
        kind=EditKind.FIX,
        finding_ids=(),
        reason="A small safe fix the rules allow.",
        fallback_note="Is this right?",
        note_section=None,
    )


def note(line: str, comment: str, section: NoteSection = NoteSection.FOR_YOU) -> PlannedEdit:
    return PlannedEdit(
        file="Controllers/UsersController.cs",
        find=line,
        replace=f"        //{comment}\n{line}",
        kind=EditKind.NOTE,
        finding_ids=(),
        reason="A note in the approved wording.",
        fallback_note=None,
        note_section=section,
    )


READONLY = fix(
    '    private string path = "users.txt";',
    '    //fix: readonly, the path never changes\n    private readonly string path = "users.txt";',
)
EXISTS = fix(
    "        var text = File.ReadAllText(path);",
    "        //fix: check the file\n        if (!File.Exists(path)) return BadRequest();\n"
    "        var text = File.ReadAllText(path);",
)


def violation(index: int = 0, **overrides: Any) -> ReviewViolation:
    values: dict[str, Any] = {
        "edit_index": index,
        "rule": 9,
        "quote": "no refactoring",
        "explanation": "A style refactor not taught.",
    }
    return ReviewViolation.model_validate(values | overrides)


def test_the_rules_are_read_by_number() -> None:
    assert sorted(RULES) == [1, 3, 4, 9]
    assert RULES[3].startswith("Never implement the core")


@pytest.mark.parametrize(
    ("edit", "what"),
    [
        (READONLY, "readonly for a fixed path"),
        (
            fix(
                "            case 2:",
                "            //fix: added the break\n            break;\n            case 2:",
            ),
            "a missing break",
        ),
        (
            fix(
                "    if (item.Id == id)\n        found = line;",
                "    if (item.Id == id)\n    {\n        found = line;\n"
                "        //fix: Found- break\n        break;\n    }",
            ),
            "a missing break",
        ),
        (
            fix(
                "        var users = Read();",
                "        //fix: Keep the id, otherwise the user is saved without it\n"
                "        user.id = id;\n        var users = Read();",
            ),
            "an id lost on update, restored with one assignment",
        ),
        (
            fix(
                "using System.Runtime.Intrinsics.Arm;\nusing System.Text.Json;",
                "using System.Text.Json;",
            ),
            "a removed stray using",
        ),
        (
            fix(
                "    public class Users : ControllerBase",
                "    //fix: Controller suffix, the route stays the same\n"
                "    public class UsersController : ControllerBase",
            ),
            "the Controller suffix on a controller class",
        ),
        (
            note(
                '            return "value";',
                "Why isn't it implemented? It should read the file and return the user with "
                "this id (or NotFound / NoContent)",
            ),
            "the GET-by-id question in the approved wording",
        ),
        (
            note(
                "        public void Put(int id, [FromBody] User user)",
                "The assignment asks PUT to return Ok(): change void to IActionResult and "
                "return Ok() at the end",
            ),
            "the PUT note in the approved wording",
        ),
        (
            note(
                "    public int id { get; set; }",
                "Properties are PascalCase (Id); renaming would stop the data file from loading, "
                "see PropertyNameCaseInsensitive",
                NoteSection.NAMES,
            ),
            "a naming note with the PropertyNameCaseInsensitive tip",
        ),
    ],
    ids=[
        "readonly",
        "break",
        "loop-break",
        "id",
        "using",
        "controller",
        "get-by-id",
        "put",
        "names",
    ],
)
def test_the_edits_the_rules_allow_outright(edit: PlannedEdit, what: str) -> None:
    assert never_a_violation(edit) == what


@pytest.mark.parametrize(
    "edit",
    [
        EXISTS,
        fix(
            "        return null;",
            "        //fix: implemented\n        return Read().First(u => u.id == id);",
        ),
        note("        return null;", "This is wrong."),
    ],
    ids=["file-exists", "implements-core", "other-note"],
)
def test_other_edits_are_not_allowed_outright(edit: PlannedEdit) -> None:
    assert never_a_violation(edit) is None


def test_a_violation_on_an_edit_the_rules_allow_is_ignored_with_the_reason() -> None:
    outcome = judge(SelfReview(violations=(violation(),)), [READONLY], RULES)

    assert outcome.broken == {}
    [reason] = outcome.ignored
    assert reason.startswith("Self-review of edit 0 (fix in Controllers/UsersController.cs)")
    assert "readonly for a fixed path is never a violation" in reason
    assert "rule 9: A style refactor not taught." in reason


def test_a_violation_that_quotes_its_rule_is_acted_on() -> None:
    flagged = violation(
        rule=4,
        quote="Never add File.Exists checks returning BadRequest",
        explanation="It adds a File.Exists check.",
    )

    outcome = judge(SelfReview(violations=(flagged,)), [EXISTS], RULES)

    assert outcome.broken == {0: "self-review: rule 4: It adds a File.Exists check."}
    assert outcome.ignored == ()


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"rule": 12}, "there is no rule 12"),
        ({"quote": "students should not do this"}, "its quote is not from rule 9"),
        ({"quote": "no"}, "its quote is not from rule 9"),
        ({"rule": 3}, "its quote is not from rule 3"),
    ],
    ids=["no-such-rule", "not-quoted", "too-short", "wrong-rule"],
)
def test_a_violation_that_does_not_quote_its_rule_is_ignored(
    overrides: dict[str, Any], problem: str
) -> None:
    outcome = judge(SelfReview(violations=(violation(**overrides),)), [EXISTS], RULES)

    assert outcome.broken == {}
    [reason] = outcome.ignored
    assert f"ignored, {problem}" in reason


def test_a_violation_of_an_edit_that_does_not_exist_is_dropped() -> None:
    outcome = judge(SelfReview(violations=(violation(index=3),)), [EXISTS], RULES)

    assert outcome == type(outcome)(broken={}, ignored=())
