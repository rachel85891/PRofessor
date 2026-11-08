"""Stage 18: the fixer reviews the files an assignment lists besides the code (ADR 0023)."""

import re
from pathlib import Path

import pytest

from hw_checker.domain import (
    EditApplication,
    EditKind,
    FixExample,
    NoteSection,
    PlannedEdit,
    PullRequestText,
    ReviewedFiles,
    ReviewRole,
    ReviewRule,
    ReviewRuleSet,
)
from hw_checker.services.fix_edits import EditRules, apply_edits, fallback_edit
from hw_checker.services.review_policy import role_policy
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    BREAK_FIX,
    COURSE,
    FILES,
    HW01_REVIEWED,
    NAMING_NOTE,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

UPDATE_JS = 'const input = document.getElementById("firstname");\n'
CLIENT_FILES = FILES | {
    "Api/wwwroot/update.html": '<input id="name">\n<script src="update.js"></script>\n',
    "Api/wwwroot/update.js": UPDATE_JS,
    "Api/wwwroot/css/site.css": "form { display: block; }\n",
    "Api/wwwroot/js/site.min.js": "var a=1;\n",
    "Api/wwwroot/lib/jquery/jquery.js": "/* a library */\n",
    "tools/build.js": "// not in wwwroot\n",
}
"""The synthetic repo with a client folder, a minified script, a library and a script
outside the client folder."""
CLIENT_ASSIGNMENT = ASSIGNMENT.model_copy(update={"reviewed_files": HW01_REVIEWED})


def _shown(prompt: str) -> list[str]:
    return re.findall(r'<student_file path="([^"]+)">', prompt)


@pytest.mark.parametrize(
    ("path", "reviewed"),
    [
        ("wwwroot/index.html", True),
        ("Api/wwwroot/js/update.js", True),
        ("Api/WWWRoot/Site.CSS", True),
        ("Api/wwwroot/js/site.min.js", False),
        ("Api/wwwroot/css/bootstrap.min.css", False),
        ("Api/wwwroot/lib/jquery/jquery.js", False),
        ("tools/build.js", False),
        ("Api/wwwroot/data.json", False),
    ],
)
def test_hw01_reviews_the_client_files_but_never_a_library(path: str, reviewed: bool) -> None:
    assert HW01_REVIEWED is not None
    assert HW01_REVIEWED.matches(path) is reviewed


def test_reviewed_files_need_at_least_one_glob() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ReviewedFiles(include=())


def test_the_prompt_shows_the_code_then_the_client_files_then_configuration_and_data(
    tmp_path: Path,
) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )

    assert _shown(prompt) == [
        "Controllers/UsersController.cs",
        "Models/User.cs",
        "Api.csproj",
        "Api/wwwroot/css/site.css",
        "Api/wwwroot/update.html",
        "Api/wwwroot/update.js",
        "appsettings.json",
        "users.json",
    ]


def test_without_reviewed_files_only_the_code_is_shown_as_before(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)

    assert not [path for path in _shown(prompt) if "wwwroot" in path]
    assert "Only `.cs` files are edited" in prompt


def _note(file: str, find: str, comment: str) -> PlannedEdit:
    return PlannedEdit(
        file=file,
        find=find,
        replace=f"{comment}\n{find}",
        kind=EditKind.NOTE,
        finding_ids=(),
        reason="A question for the student.",
        fallback_note=None,
        note_section=None,
    )


def test_only_the_files_shown_with_a_comment_style_are_edited(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    shown_script = _note("Api/wwwroot/update.js", UPDATE_JS, "//Is this the input's id?")
    library = _note("Api/wwwroot/js/site.min.js", "var a=1;\n", "//A question")
    outside = _note("tools/build.js", "// not in wwwroot\n", "//A question")
    project = _note("Api.csproj", "<Project", "<!--A question-->")

    application = setup.service.apply(
        workspace,
        [shown_script, library, outside, project],
        CLIENT_ASSIGNMENT,
        (CLIENT_ASSIGNMENT,),
    )

    assert application.applied == (shown_script,)
    assert [rejected.reason for rejected in application.rejected] == [
        "Api/wwwroot/js/site.min.js was not shown to you; only the files shown are edited",
        "tools/build.js was not shown to you; only the files shown are edited",
        "Api.csproj is not a .cs or .js or .html or .css file; only .cs or .js or .html or "
        ".css files are edited",
    ]


# ---------------------------------------------------------------- §2: comments per file type

HTML = "Api/wwwroot/update.html"
JS = "Api/wwwroot/update.js"
CSS = "Api/wwwroot/css/site.css"
CLIENT_STYLES = {
    suffix: COURSE.profile.comment_style[suffix] for suffix in (".cs", ".js", ".html", ".css")
}
PAGE = '<input id="name">\n'
SHEET = "form { display: none; }\n"


def _client_rules(*rules: ReviewRule, files: tuple[str, ...] = (JS, HTML, CSS)) -> EditRules:
    course = COURSE.model_copy(update={"rules": ReviewRuleSet(rules=(*COURSE.rules.rules, *rules))})
    policy = role_policy(course, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER, files)
    return EditRules(
        max_edits=12,
        max_fix_lines=6,
        styles=CLIENT_STYLES,
        forbidden=policy.forbidden,
        forbidden_changes=policy.forbidden_changes,
    )


def _fix(file: str, find: str, replace: str) -> PlannedEdit:
    return PlannedEdit(
        file=file,
        find=find,
        replace=replace,
        kind=EditKind.FIX,
        finding_ids=(),
        reason="The page looks for an id that is not there.",
        fallback_note="Is this the input's id?",
        note_section=None,
    )


def _reasons(files: dict[str, str], edits: list[PlannedEdit], rules: EditRules) -> list[str]:
    data = {path: text.encode() for path, text in files.items()}
    return [rejected.reason for rejected in apply_edits(data, edits, rules).application.rejected]


RENAMED_JS = UPDATE_JS.replace("firstname", "name")


@pytest.mark.parametrize(
    ("file", "find", "replace", "reason"),
    [
        (JS, UPDATE_JS, "//fix: The page's id is name\n" + RENAMED_JS, None),
        (HTML, PAGE, "<!--fix: The script looks for firstname-->\n" + PAGE, None),
        (CSS, SHEET, "/*fix: The form must show*/\nform { display: block; }\n", None),
        (
            HTML,
            PAGE,
            '//fix: The script looks for firstname\n<input id="firstname">\n',
            "a fix needs a <!--fix: ... line directly above the changed code",
        ),
        (
            JS,
            UPDATE_JS,
            "<!--fix: renamed-->\n" + RENAMED_JS,
            "a fix needs a //fix: ... line directly above the changed code",
        ),
        (
            CSS,
            SHEET,
            "//fix: The form must show\nform { display: block; }\n",
            "a fix needs a /*fix: ... line directly above the changed code",
        ),
    ],
    ids=["js", "html", "css", "html-with-slashes", "js-with-html-marker", "css-with-slashes"],
)
def test_the_fix_marker_is_checked_by_the_edited_files_style(
    file: str, find: str, replace: str, reason: str | None
) -> None:
    files = {JS: UPDATE_JS, HTML: PAGE, CSS: SHEET}
    if file == HTML and reason is None:
        replace = replace.replace(PAGE, '<input id="firstname">\n')

    reasons = _reasons(files, [_fix(file, find, replace)], _client_rules())

    assert reasons == ([] if reason is None else [reason])


def test_a_note_uses_the_files_own_comment_syntax() -> None:
    html_note = _note(HTML, PAGE, "<!--Does update.js look for this id?-->")
    js_note_in_html = _note(HTML, PAGE, "//Does update.js look for this id?")

    reasons = _reasons({HTML: PAGE}, [html_note, js_note_in_html], _client_rules())

    assert reasons == ["a note may only add <!-- comment lines and must leave the code unchanged"]


@pytest.mark.parametrize(
    ("path", "original", "valid", "invalid"),
    [
        (HTML, PAGE, "<!--A question-->", "<!--A question"),
        (CSS, SHEET, "/*A question*/", "/*A question"),
        (HTML, PAGE, "<!--A question-->", "<!--A question--> code"),
        (CSS, SHEET, "/*A question*/", "/*A question*/ code"),
        (HTML, PAGE, "<!--A question-->", "<!--A question--> code -->"),
        (CSS, SHEET, "/*A question*/", "/*A question*/ code */"),
    ],
)
def test_block_notes_require_a_complete_comment(
    path: str, original: str, valid: str, invalid: str
) -> None:
    rules = _client_rules()
    style = rules.style(path)
    assert style is not None
    assert _reasons({path: original}, [_note(path, original, valid)], rules) == []
    assert _reasons({path: original}, [_note(path, original, invalid)], rules) == [
        f"a note may only add {style.line} comment lines and must leave the code unchanged"
    ]


@pytest.mark.parametrize(
    ("path", "original", "changed", "marker", "bad_marker"),
    [
        (HTML, PAGE, '<input id="firstname">\n', "<!--fix: id-->", "<!--fix: id"),
        (CSS, SHEET, "form { display: block; }\n", "/*fix: show*/", "/*fix: show"),
    ],
)
def test_block_fix_markers_require_a_complete_comment(
    path: str, original: str, changed: str, marker: str, bad_marker: str
) -> None:
    rules = _client_rules()
    assert _reasons({path: original}, [_fix(path, original, f"{marker}\n{changed}")], rules) == []
    assert _reasons({path: original}, [_fix(path, original, f"{bad_marker}\n{changed}")], rules)
    assert _reasons(
        {path: original}, [_fix(path, original, f"{marker}\n{bad_marker}\n{changed}")], rules
    )


def test_a_fallback_note_is_written_in_the_files_comment_syntax() -> None:
    fix = _fix(HTML, PAGE, '<!--fix: x-->\n<input id="firstname">\n')

    note = fallback_edit(fix, CLIENT_STYLES[".html"])

    assert note is not None
    assert note.replace == "<!--Is this the input's id?-->\n" + PAGE


@pytest.mark.parametrize(
    ("path", "original", "marker"),
    [(HTML, PAGE, "<!--fix: id-->"), (CSS, SHEET, "/*fix: show*/")],
)
def test_a_complete_block_fix_marker_cannot_be_used_as_a_note(
    path: str, original: str, marker: str
) -> None:
    [reason] = _reasons({path: original}, [_note(path, original, marker)], _client_rules())
    assert "a note must not carry a" in reason


def test_a_cs_only_guard_is_not_applied_to_a_js_edit() -> None:
    getter = "const user = { get; set; };\n"
    cs_find = "    public int id { get; set; }\n"
    js_fix = _fix(JS, getter, "//fix: An object\nconst user = {};\n")
    cs_fix = _fix("Models/User.cs", cs_find, "    //fix: x\n    public int Id { get; set; }\n")
    files = {JS: getter, "Models/User.cs": cs_find}

    reasons = _reasons(files, [js_fix, cs_fix], _client_rules(files=(JS, "Models/User.cs")))

    assert reasons == ["a fix never changes a model's property declaration ({ get; set; })"]


def test_a_rule_limited_to_file_types_guards_only_those_files() -> None:
    limited = builders.review_rule(
        id="no-alert",
        files=[".js"],
        text="Do not add alert.",
        forbidden_text=[{"text": "alert(", "message": "no new alert"}],
    )
    page_find = "<p>Hi</p>\n"
    js_note = _note(JS, UPDATE_JS, "//Show it with alert(input.value)?")
    html_note = _note(HTML, page_find, "<!--Show it with alert(input.value)?-->")

    reasons = _reasons(
        {JS: UPDATE_JS, HTML: page_find}, [js_note, html_note], _client_rules(limited)
    )

    assert reasons == ["no new alert"]


def test_a_rule_limited_to_file_types_is_in_a_prompt_only_when_one_is_shown() -> None:
    limited = builders.review_rule(id="client-only", files=[".html", ".js"], text="Client rule.")
    course = COURSE.model_copy(
        update={"rules": ReviewRuleSet(rules=(*COURSE.rules.rules, limited))}
    )

    def rules(*files: str) -> tuple[str, ...]:
        return role_policy(course, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER, files).rules

    assert "Client rule." not in rules("Controllers/UsersController.cs", "Api.csproj")
    assert "Client rule." in rules("Controllers/UsersController.cs", JS)
    assert "Client rule." in rules(HTML)


def test_an_example_limited_to_file_types_is_shown_only_with_them() -> None:
    example = FixExample.model_validate(
        {
            "id": "client-example",
            "applies_to": ["fixer"],
            "files": [".js"],
            "situation": "An invented page.",
            "plan": '{"edits": [], "files_notes": []}',
            "origin": {"source": "developer"},
            "status": "approved",
        }
    )
    course = COURSE.model_copy(
        update={"rules": COURSE.rules.model_copy(update={"examples": (example,)})}
    )

    def situations(*files: str) -> list[str]:
        policy = role_policy(course, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER, files)
        return [shown.situation for shown in policy.examples]

    assert "An invented page." not in situations("Program.cs")
    assert "An invented page." in situations("Program.cs", JS)


def test_the_comments_rule_keeps_its_cs_wording_only_when_only_cs_is_edited() -> None:
    [comments] = [rule for rule in COURSE.rules.rules if rule.id == "comments"]

    def first_rule(*files: str) -> str:
        return role_policy(COURSE, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER, files).rules[0]

    assert first_rule("Program.cs", "Api.csproj", "users.json") == comments.wordings[0].text
    assert first_rule("Program.cs", JS) == comments.text
    assert "`//`" not in comments.text


def test_the_plan_lists_the_comment_style_of_every_file_type_shown(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )

    assert "Only `.cs`, `.js`, `.html`, `.css` files are edited" in prompt
    assert (
        "start the changed part with the fix marker line of the file's type (`//fix: ...` in "
        "`.cs` and `.js` files, `<!--fix: ...-->` in `.html` files, `/*fix: ...*/` in `.css` "
        "files)"
    ) in prompt
    assert (
        "only adds comment lines (in the file type's syntax: `//Text` in `.cs` and `.js` files; "
        "`<!--Text-->` in `.html` files; `/*Text*/` in `.css` files; no space after the "
        "comment's start)"
    ) in prompt
    assert "Never change a property declaration (`{ get; set; }`)." in prompt
    assert "a naming note (C# and JavaScript conventions)" in prompt


def test_a_prompt_without_cs_files_has_no_cs_condition(tmp_path: Path) -> None:
    client_only = {path: text for path, text in CLIENT_FILES.items() if not path.endswith(".cs")}
    setup = FixerSetup(tmp_path, files=client_only)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )

    assert "{ get; set; }" not in prompt
    assert "Only `.js`, `.html`, `.css` files are edited" in prompt
    assert "Naming (C# conventions) is in scope" not in prompt
    assert "a naming note (JavaScript conventions)" in prompt


@pytest.mark.parametrize(
    ("paths", "naming"),
    [
        (("Controllers/UsersController.cs",), "C#"),
        ((JS,), "JavaScript"),
        (("Controllers/UsersController.cs", JS, HTML, CSS), "C# and JavaScript"),
        ((HTML, CSS), ""),
    ],
)
def test_plan_naming_conventions_follow_applicable_rules(
    tmp_path: Path, paths: tuple[str, ...], naming: str
) -> None:
    files = {path: CLIENT_FILES[path] for path in paths}
    setup = FixerSetup(tmp_path, files=files)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    prompt = setup.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )
    expected = f"a naming note ({naming} conventions)" if naming else "a naming note,"
    assert expected in prompt
    assert ("Naming (C# conventions) is in scope" in prompt) == any(
        path.endswith(".cs") for path in paths
    )
    assert ("JavaScript names that are not camelCase" in prompt) == (JS in paths)


def test_omitted_client_files_do_not_contribute_policy_or_languages(tmp_path: Path) -> None:
    files = {
        "Controllers/UsersController.cs": FILES["Controllers/UsersController.cs"],
        JS: UPDATE_JS,
    }
    full = FixerSetup(tmp_path / "full", files=files)
    workspace = full.service.prepare(RUN_ID, "hw01", REPO, full.fetched)
    full_prompt = full.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )
    limited = FixerSetup(tmp_path / "limited", files=files, max_prompt_chars=len(full_prompt) - 1)
    workspace = limited.service.prepare(RUN_ID, "hw01", REPO, limited.fetched)
    prompt = limited.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )
    assert _shown(prompt) == ["Controllers/UsersController.cs"]
    assert "a naming note (C# conventions)" in prompt
    assert "JavaScript names that are not camelCase" not in prompt


# ---------------------------------------------------------------- §3: the PR text


def _named(edit: PlannedEdit, file: str, section: NoteSection | None) -> PlannedEdit:
    return edit.model_copy(update={"file": file, "note_section": section})


JS_NAMING = _named(NAMING_NOTE, JS, NoteSection.NAMES)
JS_FIX = _named(BREAK_FIX, JS, None)
HTML_FIX = _named(BREAK_FIX, HTML, None)
TEXT = PullRequestText(
    opening="Hi! Your API builds, and the sign-up page works.",
    fixed=("**A fix.** It was changed.", "**Another fix.** It was changed."),
    for_you=(),
    names=("**`New_Count`:** use camelCase.", "**`First_Name`:** use camelCase."),
)


def _body(tmp_path: Path, *edits: PlannedEdit) -> tuple[str, str]:
    setup = FixerSetup(tmp_path, replies={"writer": [TEXT]})
    application = EditApplication(applied=edits, changed_files=())

    _, body, _ = setup.service.write_text(
        RUN_ID, ASSIGNMENT, (ASSIGNMENT,), RESULTS, application, [], []
    )

    return body, setup.llm.calls[0].prompt


@pytest.mark.parametrize(
    ("edits", "fixed", "names"),
    [
        (
            (BREAK_FIX, NAMING_NOTE),
            "**Fixed in this PR (marked `//fix:` in the code):**",
            "**Names (C# conventions):**",
        ),
        (
            (JS_FIX, JS_NAMING),
            "**Fixed in this PR (marked `//fix:` in the code):**",
            "**Names (JavaScript conventions):**",
        ),
        (
            (BREAK_FIX, JS_FIX, NAMING_NOTE, JS_NAMING),
            "**Fixed in this PR (marked `//fix:` in the code):**",
            "**Names (C# and JavaScript conventions):**",
        ),
        (
            (BREAK_FIX, HTML_FIX, NAMING_NOTE, JS_NAMING),
            "**Fixed in this PR (marked `//fix:` and `<!--fix: … -->` in the code):**",
            "**Names (C# and JavaScript conventions):**",
        ),
    ],
    ids=["cs", "js", "cs-and-js", "cs-and-html"],
)
def test_the_headings_name_the_languages_and_markers_of_their_edits(
    tmp_path: Path, edits: tuple[PlannedEdit, ...], fixed: str, names: str
) -> None:
    body, _ = _body(tmp_path, *edits)

    assert fixed in body
    assert names in body


def test_the_writer_is_told_each_items_language_when_there_are_two(tmp_path: Path) -> None:
    _, one = _body(tmp_path / "one", BREAK_FIX, NAMING_NOTE)
    _, two = _body(tmp_path / "two", BREAK_FIX, JS_NAMING)

    assert "- In `UsersController.cs`: " in one
    assert "- In `UsersController.cs` (C#): " in two
    assert "- In `update.js` (JavaScript): " in two
