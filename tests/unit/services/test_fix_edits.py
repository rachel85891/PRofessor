from collections.abc import Mapping
from typing import Any

import pytest

from hw_checker.domain import EditKind, NoteSection, PlannedEdit, ReviewRole
from hw_checker.services.fix_edits import EditRules, apply_edits, fallback_edit
from hw_checker.services.review_policy import role_policy
from tests.unit.services.fixer_data import ASSIGNMENT, COURSE

CONTROLLER = "Controllers/UsersController.cs"
DATA = "users.json"
STYLES = {".cs": COURSE.profile.comment_style[".cs"]}
STYLE = STYLES[".cs"]
RULES = EditRules(
    max_edits=5,
    max_fix_lines=6,
    styles=STYLES,
    forbidden=role_policy(COURSE, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER).forbidden,
    data_files=frozenset({DATA}),
)
"""hw01's guardrails: the real course's comment style and forbidden texts."""

SWITCH = (
    "        switch (kind)\n"
    "        {\n"
    "            case 1:\n"
    "                count++;\n"
    "            case 2:\n"
    "                count--;\n"
    "                break;\n"
    "        }\n"
)
MISSING_BREAK = "                count++;\n            case 2:\n"
WITH_BREAK = (
    "                count++;\n"
    "                //fix: added the missing break, so case 1 no longer runs case 2\n"
    "                break;\n"
    "            case 2:\n"
)
MODEL = "public class User\n{\n    public int id { get; set; }\n}\n"


def edit(**overrides: Any) -> PlannedEdit:
    values: dict[str, Any] = {
        "file": CONTROLLER,
        "find": MISSING_BREAK,
        "replace": WITH_BREAK,
        "kind": EditKind.FIX,
        "finding_ids": ("hw01-update",),
        "reason": "case 1 falls through to case 2",
        "fallback_note": "Does case 1 need a break here?",
        "note_section": None,
    }
    return PlannedEdit.model_validate(values | overrides)


def files(controller: str = SWITCH, extra: Mapping[str, bytes] | None = None) -> dict[str, bytes]:
    return {CONTROLLER: controller.encode("utf-8"), DATA: b'[{"id":1}]', **(extra or {})}


def reasons(result: Any) -> list[str]:
    return [rejected.reason for rejected in result.application.rejected]


def test_a_small_marked_fix_is_applied_and_nothing_else_changes() -> None:
    result = apply_edits(files(), [edit()], RULES)

    assert result.application.applied == (edit(),)
    assert result.application.changed_files == (CONTROLLER,)
    assert result.files[CONTROLLER].decode("utf-8") == SWITCH.replace(MISSING_BREAK, WITH_BREAK)


def test_the_bom_and_crlf_line_endings_are_kept() -> None:
    crlf = "\ufeff" + SWITCH.replace("\n", "\r\n")

    result = apply_edits(files(crlf), [edit()], RULES)

    data = result.files[CONTROLLER]
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert data.decode("utf-8") == crlf.replace(
        MISSING_BREAK.replace("\n", "\r\n"), WITH_BREAK.replace("\n", "\r\n")
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"file": "Controllers/Missing.cs"}, "does not exist; edits never add, delete or rename"),
        ({"file": "../outside.cs"}, "is not a path inside the repo"),
        ({"file": "C:/x.cs"}, "is not a path inside the repo"),
        ({"file": DATA}, "is not a .cs file; only .cs files are edited"),
        ({"find": "case 9:"}, "find occurs 0 times"),
        ({"find": "count", "replace": "//fix: x\ntotal"}, "find occurs 2 times"),
        ({"find": "  "}, "find is empty"),
        ({"replace": MISSING_BREAK}, "the edit changes nothing"),
    ],
    ids=["missing-file", "dot-dot", "drive", "not-cs", "no-match", "many-matches", "empty", "same"],
)
def test_an_edit_that_does_not_fit_the_files_is_rejected(
    overrides: dict[str, Any], reason: str
) -> None:
    result = apply_edits(files(), [edit(**overrides)], RULES)

    assert result.files == {}
    [rejected] = reasons(result)
    assert reason in rejected


@pytest.mark.parametrize(
    ("replace", "reason"),
    [
        (
            "                count++;\n                break;\n            case 2:\n",
            "needs a //fix: ... line directly above the changed code",
        ),
        (
            "                count++;\n                break; //fix: added\n            case 2:\n",
            "needs a //fix: ... line directly above the changed code",
        ),
        (
            "                count++;\n                // why no break?\n            case 2:\n",
            "a fix must change code; a comment-only edit is a note",
        ),
        (
            "                //fix: rewrote\n"
            + "                count += 0;\n" * 7
            + "            case 2:\n",
            "a fix changes 8 code lines; at most 6",
        ),
        (
            "                //fix: check\n                if (File.Exists(path)) count++;\n"
            "            case 2:\n",
            "never use File.Exists",
        ),
        (
            "                //fix: reject\n"
            "                return BadRequest();\n            case 2:\n",
            "never return a new BadRequest(",
        ),
        (
            "                count++;\n                //fix: \u05ea\u05d9\u05e7\u05d5\u05df\n"
            "                break;\n            case 2:\n",
            "must be English, never Hebrew",
        ),
        (
            "                count++;\n                //fix: The lecturer asks for a break\n"
            "                break;\n            case 2:\n",
            "never mentions the lecturer",
        ),
        ("x", "needs a //fix: ... line directly above the changed code"),
        ("", "needs a //fix: ... line directly above the changed code"),
    ],
    ids=[
        "no-marker",
        "marker-after-code",
        "comment-only",
        "too-long",
        "file-exists",
        "bad-request",
        "hebrew",
        "lecturer",
        "placeholder",
        "empty",
    ],
)
def test_a_fix_that_breaks_a_rule_is_rejected(replace: str, reason: str) -> None:
    result = apply_edits(files(), [edit(replace=replace)], RULES)

    assert result.files == {}
    [rejected] = reasons(result)
    assert reason in rejected


def test_a_fix_never_changes_a_model_property_declaration() -> None:
    model = {"Models/User.cs": MODEL.encode()}
    rename = edit(
        file="Models/User.cs",
        find="    public int id { get; set; }",
        replace="    //fix: PascalCase\n    public int Id { get; set; }",
    )

    result = apply_edits(files(extra=model), [rename], RULES)

    assert reasons(result) == ["a fix never changes a model's property declaration ({ get; set; })"]


def test_a_note_adds_only_comment_lines() -> None:
    model = {"Models/User.cs": MODEL.encode()}
    note = edit(
        file="Models/User.cs",
        kind=EditKind.NOTE,
        find="    public int id { get; set; }",
        replace=(
            "    // Properties are PascalCase in C# (Id). Renaming it would stop users.json from"
            " loading;\n    // see PropertyNameCaseInsensitive.\n    public int id { get; set; }"
        ),
        fallback_note=None,
    )

    result = apply_edits(files(extra=model), [note], RULES)

    assert result.application.applied == (note,)
    assert result.files["Models/User.cs"].decode().count("//") == 2


@pytest.mark.parametrize(
    ("replace", "reason"),
    [
        ("    public int Id { get; set; }", "may only add // comment lines"),
        ("    // note\n    public int id { get; set; } = 0;", "may only add // comment lines"),
        ("    //fix: renamed\n    public int id { get; set; }", "must not carry a //fix: line"),
        ("x", "may only add // comment lines and must leave the code unchanged"),
    ],
    ids=["changes-code", "adds-code", "fix-marker", "placeholder"],
)
def test_a_note_that_changes_code_is_rejected(replace: str, reason: str) -> None:
    model = {"Models/User.cs": MODEL.encode()}
    note = edit(
        file="Models/User.cs",
        kind=EditKind.NOTE,
        find="    public int id { get; set; }",
        replace=replace,
    )

    result = apply_edits(files(extra=model), [note], RULES)

    assert reason in reasons(result)[0]


def test_a_final_newline_is_added_to_the_data_file_in_its_line_ending_style() -> None:
    data = b'[\r\n{"id":1}\r\n]'

    result = apply_edits(
        files(extra={DATA: data}), [edit(file=DATA, kind=EditKind.FINAL_NEWLINE)], RULES
    )

    assert result.files[DATA] == data + b"\r\n"


@pytest.mark.parametrize(
    ("file", "content", "reason"),
    [
        (CONTROLLER, None, "is not the data file"),
        (DATA, b"[]\n", "already ends with a newline"),
    ],
)
def test_a_final_newline_only_fixes_a_data_file_without_one(
    file: str, content: bytes | None, reason: str
) -> None:
    extra = {} if content is None else {DATA: content}

    result = apply_edits(files(extra=extra), [edit(file=file, kind=EditKind.FINAL_NEWLINE)], RULES)

    assert reason in reasons(result)[0]


@pytest.mark.parametrize("reason", ["", "   ", "x", "...", "TODO", "Placeholder.", "see above"])
@pytest.mark.parametrize("kind", [EditKind.FIX, EditKind.NOTE, EditKind.FINAL_NEWLINE])
def test_an_edit_without_a_real_reason_is_rejected(kind: EditKind, reason: str) -> None:
    planned = edit(
        kind=kind, reason=reason, file=DATA if kind is EditKind.FINAL_NEWLINE else CONTROLLER
    )

    result = apply_edits(files(), [planned], RULES)

    assert result.files == {}
    assert reasons(result) == [
        "the reason is empty or a placeholder; give one real sentence saying why"
    ]


def test_edits_over_the_limit_are_rejected() -> None:
    notes = [
        edit(kind=EditKind.NOTE, find=line, replace=f"// note {index}\n{line}")
        for index, line in enumerate(["        switch (kind)", "            case 1:", "        }"])
    ]

    result = apply_edits(files(), notes, EditRules(max_edits=2, max_fix_lines=6, styles=STYLES))

    assert len(result.application.applied) == 2
    assert reasons(result) == ["more than 2 edits in one repo"]


def test_later_edits_see_the_earlier_ones() -> None:
    first = edit()
    second = edit(
        kind=EditKind.NOTE,
        find="                break;\n            case 2:\n",
        replace=(
            "                break;\n            // Is case 2 reachable?\n            case 2:\n"
        ),
        fallback_note=None,
    )

    result = apply_edits(files(), [first, second], RULES)

    assert result.application.applied == (first, second)
    assert "// Is case 2 reachable?" in result.files[CONTROLLER].decode()


def test_a_file_with_mixed_line_endings_is_not_edited() -> None:
    result = apply_edits(files(SWITCH.replace("\n", "\r\n", 3)), [edit()], RULES)

    assert "mixes line endings" in reasons(result)[0]


def test_a_file_that_is_not_utf8_is_not_edited() -> None:
    result = apply_edits({CONTROLLER: b"\xff\xfe\x00bad"}, [edit()], RULES)

    assert reasons(result) == [f"{CONTROLLER} is not UTF-8 text"]


def test_the_fallback_of_a_fix_is_a_note_above_the_original_text() -> None:
    note = fallback_edit(edit(), STYLE)

    assert note is not None
    assert note.kind is EditKind.NOTE
    assert note.note_section is NoteSection.FOR_YOU
    assert note.replace == "                //Does case 1 need a break here?\n" + MISSING_BREAK
    result = apply_edits(files(), [note], RULES)
    assert result.application.applied == (note,)


def test_a_fix_without_a_fallback_note_has_no_fallback() -> None:
    assert fallback_edit(edit(fallback_note=None), STYLE) is None
