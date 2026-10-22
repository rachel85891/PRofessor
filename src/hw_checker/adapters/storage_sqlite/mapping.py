"""Conversions between domain models and table rows. JSON uses the models' own serialization."""

from collections.abc import Sequence
from typing import Any

from hw_checker.adapters.storage_sqlite.schema import (
    FindingRow,
    JudgedFindingRow,
    NotEvaluatedRow,
    ProposalRow,
    RepoRunRow,
    ReviewDecisionRow,
    RunRow,
)
from hw_checker.domain import (
    AssignmentResult,
    AssignmentVersion,
    Evidence,
    ExecutionResult,
    Finding,
    FindingSource,
    FixProposal,
    JudgedFinding,
    NotEvaluated,
    NotEvaluatedReason,
    Patch,
    ProposalStatus,
    RepoRunResult,
    RepoRunStatus,
    RepoTarget,
    ReviewDecision,
    ReviewOutcome,
    Run,
    RunStatus,
    Severity,
    Verdict,
)


def fill_run_row(row: RunRow, run: Run) -> None:
    row.assignment_id = run.assignment_id
    row.started_at = run.started_at
    row.status = run.status.value
    row.assignment_versions = [
        version.model_dump(mode="json") for version in run.assignment_versions
    ]


def run_from_row(row: RunRow, repo_runs: tuple[RepoRunResult, ...]) -> Run:
    return Run(
        run_id=row.run_id,
        assignment_id=row.assignment_id,
        started_at=row.started_at,
        status=RunStatus(row.status),
        repo_runs=repo_runs,
        assignment_versions=tuple(
            AssignmentVersion.model_validate(item) for item in row.assignment_versions
        ),
    )


def fill_repo_run_row(row: RepoRunRow, repo_run: RepoRunResult) -> None:
    row.repo_id = repo_run.repo.repo_id
    row.repo = repo_run.repo.model_dump(mode="json")
    row.commit_sha = repo_run.commit_sha
    row.status = repo_run.status.value
    row.execution = (
        None if repo_run.execution is None else repo_run.execution.model_dump(mode="json")
    )
    row.assignment_ids = [result.assignment_id for result in repo_run.assignment_results]
    row.score = repo_run.score
    row.errors = list(repo_run.errors)


def repo_run_from_rows(
    row: RepoRunRow,
    findings: Sequence[FindingRow],
    judged: Sequence[JudgedFindingRow],
    not_evaluated: Sequence[NotEvaluatedRow],
) -> RepoRunResult:
    results = tuple(
        AssignmentResult(
            assignment_id=assignment_id,
            findings=tuple(
                finding_from_row(item)
                for item in findings
                if item.result_assignment_id == assignment_id
            ),
            not_evaluated=tuple(
                NotEvaluated(
                    requirement_id=item.requirement_id, reason=NotEvaluatedReason(item.reason)
                )
                for item in not_evaluated
                if item.result_assignment_id == assignment_id
            ),
        )
        for assignment_id in row.assignment_ids
    )
    return RepoRunResult(
        repo_run_id=row.repo_run_id,
        repo=RepoTarget.model_validate(row.repo),
        commit_sha=row.commit_sha,
        status=RepoRunStatus(row.status),
        execution=None if row.execution is None else ExecutionResult.model_validate(row.execution),
        assignment_results=results,
        judged_findings=tuple(judged_from_row(item) for item in judged),
        score=row.score,
        errors=tuple(row.errors),
    )


def _finding_columns(finding: Finding, position: int) -> dict[str, Any]:
    return {
        "position": position,
        "finding_id": finding.id,
        "requirement_id": finding.requirement_id,
        "assignment_id": finding.assignment_id,
        "severity": finding.severity.value,
        "source": finding.source.value,
        "confidence": finding.confidence,
        "message_he": finding.message_he,
        "message_en": finding.message_en,
        "evidence": None if finding.evidence is None else finding.evidence.model_dump(mode="json"),
    }


def finding_rows(repo_run: RepoRunResult) -> list[FindingRow]:
    rows: list[FindingRow] = []
    for result in repo_run.assignment_results:
        rows.extend(
            FindingRow(
                repo_run_id=repo_run.repo_run_id,
                result_assignment_id=result.assignment_id,
                **_finding_columns(finding, position),
            )
            for position, finding in enumerate(result.findings)
        )
    return rows


def not_evaluated_rows(repo_run: RepoRunResult) -> list[NotEvaluatedRow]:
    return [
        NotEvaluatedRow(
            repo_run_id=repo_run.repo_run_id,
            result_assignment_id=result.assignment_id,
            position=position,
            requirement_id=entry.requirement_id,
            reason=entry.reason.value,
        )
        for result in repo_run.assignment_results
        for position, entry in enumerate(result.not_evaluated)
    ]


def judged_rows(repo_run_id: str, findings: Sequence[JudgedFinding]) -> list[JudgedFindingRow]:
    return [
        JudgedFindingRow(
            repo_run_id=repo_run_id,
            verdict=judged.verdict.value,
            reason=judged.reason,
            adjusted_severity=judged.adjusted_severity.value,
            **_finding_columns(judged.finding, position),
        )
        for position, judged in enumerate(findings)
    ]


def finding_from_row(row: FindingRow | JudgedFindingRow) -> Finding:
    return Finding(
        id=row.finding_id,
        requirement_id=row.requirement_id,
        assignment_id=row.assignment_id,
        severity=Severity(row.severity),
        message_he=row.message_he,
        message_en=row.message_en,
        evidence=None if row.evidence is None else Evidence.model_validate(row.evidence),
        source=FindingSource(row.source),
        confidence=row.confidence,
    )


def judged_from_row(row: JudgedFindingRow) -> JudgedFinding:
    return JudgedFinding(
        finding=finding_from_row(row),
        verdict=Verdict(row.verdict),
        reason=row.reason,
        adjusted_severity=Severity(row.adjusted_severity),
    )


def fill_proposal_row(row: ProposalRow, proposal: FixProposal) -> None:
    row.repo_run_id = proposal.repo_run_id
    row.repo = proposal.repo.model_dump(mode="json")
    row.branch_name = proposal.branch_name
    row.patches = [patch.model_dump(mode="json") for patch in proposal.patches]
    row.diff = proposal.diff
    row.verified = proposal.verified
    row.status = proposal.status.value


def proposal_from_row(row: ProposalRow) -> FixProposal:
    return FixProposal(
        proposal_id=row.proposal_id,
        repo_run_id=row.repo_run_id,
        repo=RepoTarget.model_validate(row.repo),
        branch_name=row.branch_name,
        patches=tuple(Patch.model_validate(item) for item in row.patches),
        diff=row.diff,
        verified=row.verified,
        status=ProposalStatus(row.status),
    )


def decision_row(decision: ReviewDecision) -> ReviewDecisionRow:
    return ReviewDecisionRow(
        repo_run_id=decision.repo_run_id,
        outcome=decision.outcome.value,
        reviewer_notes=decision.reviewer_notes,
    )


def decision_from_row(row: ReviewDecisionRow) -> ReviewDecision:
    return ReviewDecision(
        repo_run_id=row.repo_run_id,
        outcome=ReviewOutcome(row.outcome),
        reviewer_notes=row.reviewer_notes,
    )
