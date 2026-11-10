"""Stage 18 §4b: what the stage 17 pilot run showed. The repository findings reach the
fixer with the rule that makes them one "Files" item, and a stray `using` is deleted,
never commented out."""

from pathlib import Path

import pytest

from hw_checker.domain import AssignmentResult, EditKind, PlannedEdit, Severity
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

GITIGNORE = builders.finding(
    id="f-gitignore",
    requirement_id="gen-gitignore",
    assignment_id="_general",
    severity=Severity.MINOR,
    message_en="The repository has no .gitignore.",
)
BUILD_OUTPUT = builders.finding(
    id="f-build-output",
    requirement_id="gen-no-build-output",
    assignment_id="_general",
    severity=Severity.MINOR,
    message_en="Build output is committed: `bin/`, `obj/`.",
)
WITH_REPOSITORY = (
    AssignmentResult(assignment_id="_general", findings=(GITIGNORE, BUILD_OUTPUT)),
    *RESULTS,
)
ONE_ITEM = "one item for both findings, in the approved PRs' wording"


def _plan(tmp_path: Path, results: tuple[AssignmentResult, ...]) -> str:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)
    return setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), results, 1)


def test_the_repository_findings_reach_the_plan_with_the_files_rule(tmp_path: Path) -> None:
    prompt = _plan(tmp_path, WITH_REPOSITORY)

    assert "- `gen-gitignore` (minor): The repository has no .gitignore." in prompt
    assert "- `gen-no-build-output` (minor): Build output is committed" in prompt
    assert ONE_ITEM in prompt
    assert "`dotnet new gitignore`" in prompt


def test_without_a_repository_finding_the_files_rule_is_not_in_the_plan(tmp_path: Path) -> None:
    assert ONE_ITEM not in _plan(tmp_path, RESULTS)


USING = "using System.Text.Json;\n\npublic class UsersController\n"


def _using_fix(replace: str) -> PlannedEdit:
    return PlannedEdit(
        file="Controllers/UsersController.cs",
        find=USING,
        replace=replace,
        kind=EditKind.FIX,
        finding_ids=(),
        reason="Nothing in the file uses System.Text.Json.",
        fallback_note="Is this using needed? Nothing in the file uses it.",
        note_section=None,
    )


@pytest.mark.parametrize(
    ("replace", "reason"),
    [
        ("//fix: Removed a using nothing uses\n\npublic class UsersController\n", None),
        (
            "//fix: Commented out a using nothing uses\n//using System.Text.Json;\n\n"
            "public class UsersController\n",
            "a stray `using` is deleted, with its //fix: line where it was; never comment it out",
        ),
    ],
    ids=["deleted", "commented-out"],
)
def test_a_stray_using_is_deleted_never_commented_out(
    tmp_path: Path, replace: str, reason: str | None
) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, (ASSIGNMENT,), (), REPO, setup.fetched)

    application = setup.service.apply(workspace, [_using_fix(replace)], ASSIGNMENT, (ASSIGNMENT,))

    assert [rejected.reason for rejected in application.rejected] == (
        [] if reason is None else [reason]
    )
