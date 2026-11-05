"""Stage 18 §4: hw01's client rules (data) and the detectors of their safe fixes."""

import json
from pathlib import Path

import pytest

from hw_checker.domain import AssignmentResult, EditKind, PlannedEdit, ReviewRole
from hw_checker.services.fix_review import Allowances, never_a_violation
from hw_checker.services.review_policy import role_policy
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    COURSE,
    FILES,
    HW01_REVIEWED,
    REPO,
    RUN_ID,
    FixerSetup,
)

HTML = "Api/wwwroot/update.html"
JS = "Api/wwwroot/update.js"
INPUT = '    <input type="text" id="name">\n'
PAGE = f'<form>\n{INPUT}</form>\n<script src="update.js"></script>\n'
LOOKUP = '    const input = document.getElementById("firstname");\n'
CONDITION = "    if (res.status = 200) {\n"
SCRIPT = (
    "async function save() {\n"
    f"{LOOKUP}"
    '    const res = await fetch("api/users");\n'
    f"{CONDITION}"
    "        const user = res.json();\n"
    "    }\n"
    "}\n"
)
CLIENT_FILES = FILES | {HTML: PAGE, JS: SCRIPT}
CLIENT_ASSIGNMENT = ASSIGNMENT.model_copy(update={"reviewed_files": HW01_REVIEWED})
ELEMENT_IDS = AssignmentResult(
    assignment_id="hw01",
    findings=(
        builders.finding(
            id="f-ids",
            requirement_id="hw01-client-element-ids",
            assignment_id="hw01",
            message_en=(
                "Scripts look up an element id that no page loading them declares, so the "
                f"lookup returns null: `#firstname` ({JS}, line 2). Total: 1."
            ),
        ),
    ),
)
"""A synthetic `hw01-client-element-ids` finding that names `firstname`."""


def _fix(file: str, find: str, replace: str) -> PlannedEdit:
    return PlannedEdit(
        file=file,
        find=find,
        replace=replace,
        kind=EditKind.FIX,
        finding_ids=(),
        reason="The page and the script do not agree.",
        fallback_note="Do the page and the script agree here?",
        note_section=None,
    )


HTML_ID_FIX = _fix(
    HTML,
    INPUT,
    '    <!--fix: update.js looks for firstname-->\n    <input type="text" id="firstname">\n',
)
JS_ID_FIX = _fix(
    JS, LOOKUP, "    //fix: The page's id is name\n" + LOOKUP.replace("firstname", "name")
)
SRC_FIX = _fix(
    HTML,
    '<script src="update.js"></script>\n',
    '<!--fix: x-->\n<script src="Update.js"></script>\n',
)
CONDITION_FIX = _fix(JS, CONDITION, "    //fix: === compares\n    if (res.status === 200) {\n")
CONDITION_AND_MORE = _fix(
    JS, CONDITION, "    //fix: === compares\n    if (res.status === 200 && res.ok) {\n"
)
NO_REFERENCE = "the reference `{was}` is changed to `{now}`, but no check's finding names it"
CONDITION_ONLY = (
    "an `=` in an `if` condition is fixed only by changing it to `===`; nothing else on the "
    "line changes"
)


def _apply(
    tmp_path: Path, edits: list[PlannedEdit], results: tuple[AssignmentResult, ...]
) -> list[str]:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    application = setup.service.apply(
        workspace, edits, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), results
    )
    return [rejected.reason for rejected in application.rejected]


@pytest.mark.parametrize("edit", [HTML_ID_FIX, JS_ID_FIX], ids=["html-id", "js-lookup"])
def test_a_reference_a_finding_names_is_fixed(tmp_path: Path, edit: PlannedEdit) -> None:
    assert _apply(tmp_path, [edit], (ELEMENT_IDS,)) == []


@pytest.mark.parametrize(
    ("edit", "was", "now"),
    [(HTML_ID_FIX, "name", "firstname"), (SRC_FIX, "update.js", "Update.js")],
    ids=["no-finding", "not-named"],
)
def test_a_reference_no_finding_names_is_rejected(
    tmp_path: Path, edit: PlannedEdit, was: str, now: str
) -> None:
    results = (ELEMENT_IDS,) if edit is SRC_FIX else ()

    [reason] = _apply(tmp_path, [edit], results)

    assert reason.startswith(NO_REFERENCE.format(was=was, now=now))


def test_an_assignment_in_a_condition_becomes_strict_equality(tmp_path: Path) -> None:
    assert _apply(tmp_path, [CONDITION_FIX], ()) == []


def test_strict_equality_with_more_changes_on_the_line_is_rejected(tmp_path: Path) -> None:
    assert _apply(tmp_path, [CONDITION_AND_MORE], ()) == [CONDITION_ONLY]


def test_the_safe_client_fixes_are_never_a_violation_for_the_judge() -> None:
    policy = role_policy(COURSE, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), ReviewRole.JUDGE, (JS,))
    allowances = Allowances(
        detectors=policy.detectors,
        approved_notes=policy.approved_notes,
        styles=COURSE.profile.comment_style,
        findings={"hw01-client-element-ids": (ELEMENT_IDS.findings[0].message_en,)},
    )
    without_finding = Allowances(
        detectors=policy.detectors, approved_notes=(), styles=COURSE.profile.comment_style
    )

    assert never_a_violation(CONDITION_FIX, allowances) == "= changed to === in an if condition"
    assert never_a_violation(JS_ID_FIX, allowances) == (
        "a file or element reference a check's finding names"
    )
    assert never_a_violation(JS_ID_FIX, without_finding) is None
    assert never_a_violation(CONDITION_AND_MORE, allowances) is None


def test_the_client_fixes_are_not_allowed_in_a_cs_file() -> None:
    policy = role_policy(COURSE, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), ReviewRole.JUDGE, (JS,))
    allowances = Allowances(
        detectors=policy.detectors, approved_notes=(), styles=COURSE.profile.comment_style
    )
    in_cs = CONDITION_FIX.model_copy(update={"file": "Controllers/UsersController.cs"})

    assert never_a_violation(in_cs, allowances) is None


def test_hw01s_client_rules_are_in_the_prompt_only_with_client_files() -> None:
    def rules(*files: str) -> str:
        policy = role_policy(COURSE, ASSIGNMENT, (ASSIGNMENT,), ReviewRole.FIXER, files)
        return "\n".join(policy.rules)

    cs_only = rules("Controllers/UsersController.cs")
    with_client = rules("Controllers/UsersController.cs", HTML, JS)
    with_css = rules("Controllers/UsersController.cs", "Api/wwwroot/site.css")

    assert "onclick=" not in cs_only
    assert "onclick=" in with_client
    assert "really breaks the page" in with_css
    assert "onclick=" not in with_css


def test_hw01s_client_example_passes_the_guardrails(tmp_path: Path) -> None:
    [example] = [e for e in ASSIGNMENT.review_rules.examples if e.id == "client-id-condition-await"]
    edits = [PlannedEdit.model_validate(edit) for edit in json.loads(example.plan)["edits"]]
    files = FILES | {
        "wwwroot/update.html": PAGE,
        "wwwroot/update.js": SCRIPT,
    }
    setup = FixerSetup(tmp_path, files=files)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    application = setup.service.apply(
        workspace, edits, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), (ELEMENT_IDS,)
    )

    assert application.rejected == ()
    assert len(application.applied) == len(edits) == 3
