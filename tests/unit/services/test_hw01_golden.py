"""Golden test: hw01's rendered prompts and PR text (stage 3f, ADR 0023).

The golden files were rendered before the course policy moved out of code and prompts into
`course/` and `assignments/hw01/`; the same synthetic inputs must give byte-identical output.
Only the developer accepts a change to a golden file.
"""

from pathlib import Path

import pytest

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditApplication,
    EditKind,
    NoteSection,
    PlannedEdit,
    PullRequestText,
    RequirementKind,
    Severity,
)
from hw_checker.services.fixing import pr_title
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    BREAK_FIX,
    CONTROLLER,
    DATA,
    NAMING_NOTE,
    REPO,
    RUN_ID,
    FixerSetup,
)

GOLDEN = Path(__file__).parent / "golden" / "hw01"

GENERAL = Assignment(
    id="_general",
    order=0,
    title="General requirements",
    requirements=(
        builders.requirement(
            id="gen-build",
            title="The project builds",
            severity=Severity.CRITICAL,
            kind=RequirementKind.BUILD,
        ),
    ),
    lenient_notes=("Storing data in a text file is expected at this stage",),
)
HW01 = Assignment(
    id="hw01",
    order=1,
    title="Users API",
    requirements=(
        builders.requirement(
            id="hw01-update",
            title="PUT updates a user",
            description="The updated user is saved",
            severity=Severity.MAJOR,
            kind=RequirementKind.BUILD,
            allowed_alternatives=("Returning 204 is acceptable",),
        ),
        builders.requirement(
            id="hw01-naming",
            title="Names follow C# conventions",
            severity=Severity.MINOR,
            kind=RequirementKind.BUILD,
        ),
    ),
    taught_techniques=("controllers", "routing attributes"),
)
HW01_UNTAUGHT = HW01.model_copy(update={"taught_techniques": ()})
ASSIGNMENTS = (GENERAL, HW01)
FINDING = builders.finding(
    id="f-1",
    requirement_id="hw01-update",
    assignment_id="hw01",
    severity=Severity.MAJOR,
    message_en="Updating with kind 1 also runs kind 2.",
    evidence=builders.evidence(file_path=CONTROLLER, line_start=10, line_end=12, quote="case 1:"),
)
RESULTS = (
    AssignmentResult(assignment_id="_general"),
    AssignmentResult(assignment_id="hw01", findings=(FINDING,)),
)
PUT_NOTE = PlannedEdit(
    file=CONTROLLER,
    find="    public string Update(int kind, int count)\n",
    replace=(
        "    //The assignment asks PUT to return Ok(): change void to IActionResult and return"
        " Ok() at the end\n"
        "    public string Update(int kind, int count)\n"
    ),
    kind=EditKind.NOTE,
    finding_ids=(),
    reason="The assignment asks PUT to return Ok().",
    fallback_note=None,
    note_section=NoteSection.FOR_YOU,
)
NEWLINE = PlannedEdit(
    file=DATA,
    find="",
    replace="",
    kind=EditKind.FINAL_NEWLINE,
    finding_ids=(),
    reason="The last line has no newline, so the next append joins it.",
    fallback_note=None,
    note_section=None,
)
EDITS = (BREAK_FIX, PUT_NOTE, NAMING_NOTE, NEWLINE)
PR_TEXT = PullRequestText(
    opening="Your API builds, and creating users works.",
    fixed=(
        "**`case 1` ran `case 2` too.** A `break` was missing; now it is there.",
        "**`users.json` did not end with a new line.** A new line was added at the end.",
    ),
    for_you=("**PUT:** The assignment asks PUT to return `Ok()`.",),
    names=("**`New_Count`:** local variables are written in camelCase: `newCount`.",),
)
FILES_NOTES = ["Class1.cs looks like a leftover from the project template; is it still needed?"]


def _golden(name: str) -> str:
    return (GOLDEN / name).read_bytes().decode("utf-8")


def test_the_fixer_plan_prompt_of_the_first_attempt_is_unchanged(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, HW01, ASSIGNMENTS, RESULTS, 1)

    assert prompt == _golden("fixer_plan_attempt1.md")


def test_the_fixer_plan_prompt_of_a_retry_with_kept_edits_is_unchanged(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(
        workspace,
        HW01,
        ASSIGNMENTS,
        RESULTS,
        2,
        ["Rejected fix in Controllers/UsersController.cs: find occurs 2 times"],
        [BREAK_FIX, NAMING_NOTE],
    )

    assert prompt == _golden("fixer_plan_retry.md")


def test_the_self_review_prompt_is_unchanged(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    application = setup.service.apply(workspace, EDITS)

    setup.service.self_review(RUN_ID, workspace, HW01_UNTAUGHT, application)

    [call] = setup.llm.calls
    assert call.prompt == _golden("fixer_review.md")


def test_the_writer_prompt_and_the_pr_body_are_unchanged(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, replies={"writer": [PR_TEXT]})
    application = EditApplication(applied=EDITS, changed_files=(CONTROLLER, DATA))

    title, body, _ = setup.service.write_text(
        RUN_ID, HW01, ASSIGNMENTS, RESULTS, application, FILES_NOTES, ["hw01-update"]
    )

    [call] = setup.llm.calls
    assert call.prompt == _golden("pr_text.md")
    assert body == _golden("pr_body.md")
    assert title == _golden("pr_titles.txt").splitlines()[0]


@pytest.mark.parametrize(
    ("cleared", "line"), [(("hw01-update",), 0), (("hw01-naming",), 1), ((), 1)]
)
def test_both_pr_titles_are_unchanged(cleared: tuple[str, ...], line: int) -> None:
    assert pr_title(HW01, cleared, ASSIGNMENTS) == _golden("pr_titles.txt").splitlines()[line]
