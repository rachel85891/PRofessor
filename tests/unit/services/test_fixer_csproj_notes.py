"""A layer-reference finding is noted on its `<ProjectReference>` line, in the `.csproj`, as
an XML comment (developer decision 2026-11-19, HW04 round review). `.csproj` files get notes
only, and only when such a finding exists; other prompts do not show the style."""

from pathlib import Path

from hw_checker.domain import AssignmentResult, EditKind, PlannedEdit, Severity
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    FILES,
    REPO,
    RESULTS,
    RUN_ID,
    SOURCE,
    FixerSetup,
)

HW02 = next(a for a in SOURCE.load_all() if a.id == "hw02")
HW03 = next(a for a in SOURCE.load_all() if a.id == "hw03")
SELECTED = (ASSIGNMENT, HW02, HW03)
PROJECT = "Api.csproj"
REFERENCE = '    <ProjectReference Include="..\\Repositories\\Repositories.csproj" />\n'
PROJECT_TEXT = (
    f'<Project Sdk="Microsoft.NET.Sdk.Web">\n  <ItemGroup>\n{REFERENCE}  </ItemGroup>\n</Project>\n'
)
REPO_FILES = {**FILES, PROJECT: PROJECT_TEXT}
WRONG_REFERENCE = builders.finding(
    id="f-references",
    requirement_id="hw03-references",
    assignment_id="hw03",
    severity=Severity.MAJOR,
    message_en=(
        "The project references do not follow the layers: `Api` references `Repositories` "
        "(api → repositories is not allowed) (Api.csproj, line 3). Total: 1."
    ),
    evidence=builders.evidence(
        file_path=PROJECT, line_start=3, line_end=3, quote=REFERENCE.strip()
    ),
)
WITH_REFERENCE = (*RESULTS, AssignmentResult(assignment_id="hw03", findings=(WRONG_REFERENCE,)))
NOTE = PlannedEdit(
    file=PROJECT,
    find=REFERENCE,
    replace=(
        "    <!--The assignment asks the controller to use the service, not the repository: "
        "the API project references Services, and Services references Repositories-->\n"
        f"{REFERENCE}"
    ),
    kind=EditKind.NOTE,
    finding_ids=("hw03-references",),
    reason="The API project references Repositories directly.",
    fallback_note=None,
    note_section=None,
)
FIX = NOTE.model_copy(
    update={
        "kind": EditKind.FIX,
        "replace": "    <!--fix: removed the reference that skips Services-->\n",
        "finding_ids": (),
        "fallback_note": "Should the API project reference Repositories?",
    }
)


def _apply(
    tmp_path: Path, edit: PlannedEdit, results: tuple[AssignmentResult, ...]
) -> tuple[str, ...]:
    setup = FixerSetup(tmp_path, files=REPO_FILES)
    workspace = setup.service.prepare(RUN_ID, SELECTED, results, REPO, setup.fetched)
    application = setup.service.apply(workspace, [edit], HW03, SELECTED, results)
    return tuple(rejected.reason for rejected in application.rejected)


def _plan(tmp_path: Path, results: tuple[AssignmentResult, ...]) -> str:
    setup = FixerSetup(tmp_path, files=REPO_FILES)
    workspace = setup.service.prepare(RUN_ID, SELECTED, results, REPO, setup.fetched)
    return setup.service.plan_prompt(workspace, HW03, SELECTED, results, 1)


def test_a_note_on_the_project_reference_line_is_applied(tmp_path: Path) -> None:
    assert _apply(tmp_path, NOTE, WITH_REFERENCE) == ()


def test_a_fix_in_a_csproj_is_refused(tmp_path: Path) -> None:
    assert _apply(tmp_path, FIX, WITH_REFERENCE) == (
        "Api.csproj gets notes only; plan a note, not a fix",
    )


def test_without_a_layer_reference_finding_a_csproj_is_not_edited(tmp_path: Path) -> None:
    [reason] = _apply(tmp_path, NOTE, RESULTS)

    assert reason.startswith("Api.csproj is not a ")


def test_the_plan_shows_the_reference_line_and_the_xml_comment_style(tmp_path: Path) -> None:
    prompt = _plan(tmp_path, WITH_REFERENCE)

    assert "(api → repositories is not allowed) (Api.csproj, line 3)" in prompt
    assert "[Api.csproj:3-3]" in prompt
    assert "`.csproj` files get notes only, never a `fix`." in prompt
    assert "`//Text` in `.cs` files; `<!--Text-->` in `.csproj` files" in prompt
    # The fix marker leaves the notes-only type out.
    assert "start the changed part with a `//fix: ...` line" in prompt
    assert "A layer-reference finding is noted on the `<ProjectReference>` line" in prompt
    assert "registrations are never questioned for it" in prompt


def test_without_the_finding_the_plan_has_no_csproj_style(tmp_path: Path) -> None:
    prompt = _plan(tmp_path, RESULTS)

    assert "files get notes only" not in prompt
    assert "`.csproj`" not in prompt.split("# Examples")[0]
