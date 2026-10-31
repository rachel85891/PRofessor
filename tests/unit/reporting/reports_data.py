"""Fixed report inputs for the reporting tests; every name is invented."""

from datetime import UTC, datetime
from pathlib import Path

from hw_checker.domain import (
    AssignmentResult,
    ExecutionResult,
    FailureKind,
    FindingSource,
    NotEvaluated,
    NotEvaluatedReason,
    RepoRunResult,
    RepoRunStatus,
    RepoSummary,
    RepoTarget,
    RosterWarning,
    RosterWarningKind,
    RunStatus,
    RunSummary,
    Severity,
    StepStatus,
    Student,
)
from hw_checker.ports.reporting import RepoReport, RoundReport
from tests.unit.domain import builders

RUN_ID = "20261029-090000-abc123"
TITLES = {
    "hw01-create": "POST creates a user",
    "hw01-login": "Login with correct details succeeds",
    "hw01-update": "PUT updates a user",
    "hw01-read-back": "The created user can be read back by id (the data is saved)",
    "gen-naming-methods": "Method names are PascalCase",
}


def repo(name: str, owner: str | None = None) -> RepoTarget:
    owner = owner or name.split()[0].lower()
    url = f"https://github.com/{owner}/webapi"
    return RepoTarget(
        repo_id=f"{owner}__webapi",
        owner=owner,
        name="webapi",
        url=url,
        student=Student(name=name, github_url=url),
        pair_id=f"{owner}__webapi",
    )


def full_repo_run() -> RepoRunResult:
    """Findings of all three severities, not-evaluated entries, evidence and an error."""
    return RepoRunResult(
        repo_run_id=f"{RUN_ID}/noa__webapi",
        repo=repo("Noa Testman"),
        commit_sha="0123456" + "0" * 33,
        status=RepoRunStatus.FAILED,
        execution=ExecutionResult(
            build_status=StepStatus.SUCCEEDED,
            run_status=StepStatus.FAILED,
            failure_kind=FailureKind.STARTUP_FAILED,
            target_framework="net8.0",
            framework_outdated=True,
        ),
        assignment_results=(
            AssignmentResult(
                assignment_id="_general",
                findings=(
                    builders.finding(
                        id="f-3",
                        requirement_id="gen-naming-methods",
                        assignment_id="_general",
                        severity=Severity.MINOR,
                        message_en="The method getUser should be named GetUser.",
                        evidence=builders.evidence(
                            file_path="Controllers/UsersController.cs",
                            line_start=21,
                            line_end=21,
                            quote="public IActionResult getUser(int id)",
                        ),
                        source=FindingSource.DETERMINISTIC,
                        confidence=1.0,
                    ),
                ),
            ),
            AssignmentResult(
                assignment_id="hw01",
                findings=(
                    builders.finding(
                        id="f-1",
                        requirement_id="hw01-create",
                        assignment_id="hw01",
                        severity=Severity.CRITICAL,
                        message_en="POST /api/users: expected 201, got 500.\nThe app threw.",
                        evidence=None,
                        source=FindingSource.DETERMINISTIC,
                        confidence=1.0,
                    ),
                    builders.finding(
                        id="f-2",
                        requirement_id="hw01-update",
                        assignment_id="hw01",
                        severity=Severity.MAJOR,
                        message_en="PUT ignores the id in the route.",
                        evidence=builders.evidence(
                            file_path="Services/UserService.cs",
                            line_start=40,
                            line_end=42,
                            quote="users[index] = user;\n// uses ``user.Id``\nSave();",
                        ),
                    ),
                ),
                not_evaluated=(
                    NotEvaluated(
                        requirement_id="hw01-login", reason=NotEvaluatedReason.RUN_NOT_HEALTHY
                    ),
                    NotEvaluated(
                        requirement_id="hw01-read-back",
                        reason=NotEvaluatedReason.DEPENDENCY_FAILED,
                    ),
                ),
            ),
        ),
        errors=("assignment_hw01: unexpected RuntimeError: boom",),
    )


def full_report() -> RepoReport:
    return RepoReport(
        run_id=RUN_ID,
        assignment_id="hw01",
        repo_run=full_repo_run(),
        partner="Yael Testberg",
        requirement_titles=TITLES,
    )


def clean_report() -> RepoReport:
    return RepoReport(
        run_id=RUN_ID,
        assignment_id="hw01",
        repo_run=RepoRunResult(
            repo_run_id=f"{RUN_ID}/tamar__webapi",
            repo=repo("Tamar Testson"),
            commit_sha="fedcba9" + "0" * 33,
            status=RepoRunStatus.DONE,
            execution=ExecutionResult(
                build_status=StepStatus.SUCCEEDED,
                run_status=StepStatus.SUCCEEDED,
                target_framework="net9.0",
            ),
            assignment_results=(AssignmentResult(assignment_id="hw01"),),
        ),
        requirement_titles=TITLES,
    )


ROSTER_ORDER = (repo("Tamar Testson"), repo("Noa Testman"), repo("Lea Testkin"))
"""Lea's result was not saved; Noa finished before Tamar."""


def round_report(reports_dir: Path) -> RoundReport:
    noa = RepoSummary(
        repo_id="noa__webapi",
        status=RepoRunStatus.FAILED,
        critical=1,
        major=1,
        minor=1,
        not_evaluated=2,
        report_path=reports_dir / "noa__webapi.md",
    )
    tamar = RepoSummary(
        repo_id="tamar__webapi",
        status=RepoRunStatus.DONE,
        report_path=reports_dir / "tamar__webapi.md",
    )
    return RoundReport(
        summary=RunSummary(
            run_id=RUN_ID,
            assignment_id="hw01",
            status=RunStatus.COMPLETED,
            started_at=datetime(2026, 10, 29, 9, 0, tzinfo=UTC),
            repos=(noa, tamar),
        ),
        repos=ROSTER_ORDER,
        partners={"noa__webapi": "Yael Testberg"},
        roster_warnings=(
            RosterWarning(
                kind=RosterWarningKind.INVALID_URL,
                row=7,
                message="Row 7 (Dina Testwitz): 'none' is not a GitHub repo URL.",
            ),
        ),
    )
