"""Fully populated domain objects for storage tests: every optional field set, Hebrew text."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from hw_checker.domain import (
    AssignmentResult,
    AssignmentVersion,
    Evidence,
    ExecutionResult,
    FailureKind,
    Finding,
    FindingSource,
    FixProposal,
    HttpMethod,
    HttpProbe,
    JudgedFinding,
    Patch,
    ProbeResult,
    ProposalStatus,
    RepoRunResult,
    RepoRunStatus,
    RepoTarget,
    ReviewDecision,
    ReviewOutcome,
    Run,
    RunStatus,
    Severity,
    StepStatus,
    Student,
    Verdict,
)

ISRAEL = timezone(timedelta(hours=3))
HEBREW_MESSAGE = "הפעולה מחזירה 200 עם null במקום 404"


def repo_target(owner: str) -> RepoTarget:
    url = f"https://github.com/{owner}/api"
    return RepoTarget(
        repo_id=f"{owner}__api",
        owner=owner,
        name="api",
        url=url,
        student=Student(name=f"תלמיד {owner}", github_url=url),
        pair_id=f"{owner}__api",
    )


def finding(finding_id: str, severity: Severity = Severity.MAJOR, *, llm: bool = True) -> Finding:
    return Finding(
        id=finding_id,
        requirement_id="hw01-r2",
        assignment_id="hw01",
        severity=severity,
        message_he=HEBREW_MESSAGE,
        message_en="The action returns 200 with null instead of 404.",
        evidence=Evidence(
            file_path="Controllers/ProductsController.cs",
            line_start=19,
            line_end=21,
            quote="return Ok(product);",
        )
        if llm
        else None,
        source=FindingSource.LLM if llm else FindingSource.DETERMINISTIC,
        confidence=0.875,
    )


def probe(path: str, status: int) -> HttpProbe:
    return HttpProbe(
        requirement_id="hw01-r3",
        assignment_id="hw01",
        method=HttpMethod.POST,
        path=path,
        body='{"name":"שולחן","price":250}',
        expected_status=status,
        allowed_statuses=(status, 200),
        notes="Body must be JSON",
    )


def successful_execution() -> ExecutionResult:
    return ExecutionResult(
        build_status=StepStatus.SUCCEEDED,
        run_status=StepStatus.SUCCEEDED,
        logs_excerpt="info: Microsoft.Hosting.Lifetime[14]\n      Now listening on: http://[::]:8080",
        logs_path=Path("workspace") / "logs" / "run-1" / "dana__api",
        probe_results=(
            ProbeResult(probe=probe("/api/products", 201), actual_status=201, passed=True),
            ProbeResult(
                probe=probe("/api/products/1", 204),
                actual_status=None,
                passed=False,
                error="timed out",
            ),
        ),
    )


def failed_build() -> ExecutionResult:
    return ExecutionResult(
        build_status=StepStatus.FAILED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
        logs_excerpt="Program.cs(3,1): error CS0103: The name 'x' does not exist",
        logs_path=Path("workspace") / "logs" / "run-1" / "noa__api",
    )


def judged(
    finding_id: str,
    verdict: Verdict,
    adjusted: Severity | None = None,
    severity: Severity = Severity.MAJOR,
) -> JudgedFinding:
    return JudgedFinding(
        finding=finding(finding_id, severity),
        verdict=verdict,
        reason="מקובל לפי הדרישה" if verdict is Verdict.ACCEPTED else "Allowed alternative",
        adjusted_severity=adjusted or severity,
    )


def repo_run_with_findings(run_id: str = "run-1") -> RepoRunResult:
    return RepoRunResult(
        repo_run_id=f"{run_id}/dana__api",
        repo=repo_target("dana"),
        commit_sha="0f6db0d" + "a" * 33,
        status=RepoRunStatus.DONE,
        execution=successful_execution(),
        assignment_results=(
            AssignmentResult(assignment_id="_general"),
            AssignmentResult(
                assignment_id="hw01",
                findings=(finding("f-1"), finding("f-2", Severity.MINOR, llm=False)),
            ),
        ),
        judged_findings=(
            judged("f-1", Verdict.ACCEPTED, adjusted=Severity.MINOR, severity=Severity.CRITICAL),
            judged("f-2", Verdict.REJECTED, severity=Severity.MINOR),
        ),
        score=87.5,
        errors=("Partner hint skipped: partner repo failed",),
    )


def failed_repo_run(run_id: str = "run-1") -> RepoRunResult:
    return RepoRunResult(
        repo_run_id=f"{run_id}/noa__api",
        repo=repo_target("noa"),
        commit_sha="1" * 40,
        status=RepoRunStatus.FAILED,
        execution=failed_build(),
        assignment_results=(AssignmentResult(assignment_id="hw01"),),
        score=0.0,
        errors=("build failed",),
    )


def proposal_repo_run(run_id: str = "run-1") -> RepoRunResult:
    return RepoRunResult(
        repo_run_id=f"{run_id}/avi__api",
        repo=repo_target("avi"),
        commit_sha="2" * 40,
        status=RepoRunStatus.DONE,
        execution=successful_execution(),
        assignment_results=(AssignmentResult(assignment_id="hw01", findings=(finding("f-3"),)),),
        judged_findings=(judged("f-3", Verdict.ACCEPTED),),
        score=92.0,
    )


def full_run(run_id: str = "run-1", started_at: datetime | None = None) -> Run:
    return Run(
        run_id=run_id,
        assignment_id="hw01",
        started_at=started_at or datetime(2026, 10, 21, 12, 30, 15, 250000, tzinfo=ISRAEL),
        status=RunStatus.COMPLETED,
        repo_runs=(
            failed_repo_run(run_id),
            repo_run_with_findings(run_id),
            proposal_repo_run(run_id),
        ),
        assignment_versions=(
            AssignmentVersion(assignment_id="_general", source_hash="a" * 64),
            AssignmentVersion(assignment_id="hw01", source_hash="b" * 64),
        ),
    )


def empty_run(run_id: str, started_at: datetime) -> Run:
    return Run(run_id=run_id, assignment_id="hw01", started_at=started_at, status=RunStatus.RUNNING)


def proposal(proposal_id: str, repo_run_id: str, status: ProposalStatus) -> FixProposal:
    return FixProposal(
        proposal_id=proposal_id,
        repo_run_id=repo_run_id,
        repo=repo_target("avi"),
        branch_name="hw-review/hw01-2026-10-21",
        patches=(
            Patch(
                file_path="Controllers/ProductsController.cs",
                search="return Ok(product);",
                replace="return product is null ? NotFound() : Ok(product);",
                requirement_id="hw01-r2",
                explanation="Return 404 when the product does not exist.",
            ),
        ),
        diff="--- a/Controllers/ProductsController.cs\n+++ b/Controllers/ProductsController.cs\n",
        verified=True,
        status=status,
    )


def decision(repo_run_id: str, outcome: ReviewOutcome, notes: str = "") -> ReviewDecision:
    return ReviewDecision(repo_run_id=repo_run_id, outcome=outcome, reviewer_notes=notes)


STARTED = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
