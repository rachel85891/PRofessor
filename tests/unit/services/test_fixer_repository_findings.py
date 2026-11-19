"""Stage 18 §4b: what the stage 17 pilot run showed. The repository findings reach the
fixer with the rule that makes them one "Files" item, and a stray `using` is deleted,
never commented out."""

from pathlib import Path

import pytest

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    AssignmentResult,
    CheckInput,
    EditApplication,
    EditKind,
    Finding,
    PlannedEdit,
    Severity,
    TrackedFiles,
)
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    BREAK_FIX,
    REPO,
    RESULTS,
    RUN_ID,
    SOURCE,
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
ONE_ITEM = "one item for both findings, in the approved PRs' style"


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


# ------------------------- the Files item names what was found (2026-11-19, HW04 round)

GENERAL = next(a for a in SOURCE.load_all() if a.id == "_general")
REGISTRY = CheckRegistry(builtin_checks())
NAMES_WHAT_WAS_FOUND = (
    "The item names the files and folders the findings list, and calls each what it is: "
    "`bin/` and `obj/` are build output, `.vs/` and `*.user` files are Visual Studio files"
)
ONLY_WITHOUT_GITIGNORE = (
    "only when the `gen-gitignore` finding says the repository has no `.gitignore`; when it "
    "has one that misses entries, say which entries to add to it"
)


def _general_findings(tmp_path: Path, tracked: tuple[str, ...]) -> tuple[Finding, ...]:
    """The real `_general` repository checks on a repo whose tracked files are `tracked`
    and whose `.gitignore` leaves none of them out."""
    findings: list[Finding] = []
    for requirement in GENERAL.requirements:
        if requirement.id not in ("gen-gitignore", "gen-no-build-output"):
            continue
        registered = REGISTRY.get(requirement.check or "")
        params = registered.spec.params_model.model_validate(dict(requirement.params))
        check_input = CheckInput(
            repo_dir=tmp_path,
            assignment=GENERAL,
            requirement=requirement,
            tracked=TrackedFiles(paths=tracked),
        )
        outcome = registered.function(check_input, params)
        assert isinstance(outcome, tuple)
        findings += [finding for finding in outcome if isinstance(finding, Finding)]
    return tuple(findings)


def test_with_only_vs_committed_and_a_gitignore_the_findings_name_vs(tmp_path: Path) -> None:
    tracked = (".gitignore", "Api/Api.csproj", ".vs/Api/v17/.suo")

    findings = _general_findings(tmp_path, tracked)

    assert [finding.requirement_id for finding in findings] == [
        "gen-gitignore",
        "gen-no-build-output",
    ]
    for finding in findings:
        assert "`.vs/`" in finding.message_en
        assert "bin/" not in finding.message_en and "obj/" not in finding.message_en
        assert "Add a `.gitignore`" not in finding.message_en
    assert "Add `.vs/` to the `.gitignore`" in findings[0].message_en
    prompt = _plan(
        tmp_path / "fixer",
        (AssignmentResult(assignment_id="_general", findings=findings), *RESULTS),
    )
    assert NAMES_WHAT_WAS_FOUND in prompt
    assert ONLY_WITHOUT_GITIGNORE in prompt
    assert "These are in the repository and no `.gitignore` leaves them out: `.vs/`" in prompt
    assert "The `bin/` and `obj/` folders are in the repository" not in prompt


def test_the_writer_prompt_shows_the_files_rule(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    application = EditApplication(applied=(BREAK_FIX,), changed_files=(BREAK_FIX.file,))

    setup.service.write_text(
        RUN_ID, ASSIGNMENT, (ASSIGNMENT,), WITH_REPOSITORY, application, [], ["hw01-update"]
    )

    writer = setup.llm.calls[0].prompt
    assert NAMES_WHAT_WAS_FOUND in writer
    assert ONLY_WITHOUT_GITIGNORE in writer
