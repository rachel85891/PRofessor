"""Domain layer: pure models and business rules (repo, pair, requirement, finding).

May depend on: the standard library and Pydantic only.
"""

from hw_checker.domain.assignment import (
    Assignment,
    HttpMethod,
    HttpProbe,
    ParamValue,
    Requirement,
    RequirementKind,
    Severity,
)
from hw_checker.domain.checks import CheckDescription, CheckInput, CheckParamDescription
from hw_checker.domain.execution import (
    ExecutionRequest,
    ExecutionResult,
    FailureKind,
    ProbeResult,
    StepStatus,
)
from hw_checker.domain.findings import (
    Evidence,
    Finding,
    FindingSource,
    JudgedFinding,
    Verdict,
)
from hw_checker.domain.proposal import (
    FixProposal,
    Patch,
    ProposalStatus,
    ReviewDecision,
    ReviewOutcome,
)
from hw_checker.domain.roster import (
    GitHubRepo,
    Pair,
    RepoTarget,
    RosterResult,
    RosterRow,
    RosterWarning,
    RosterWarningKind,
    Student,
    parse_github_url,
)
from hw_checker.domain.run import (
    AssignmentResult,
    AssignmentVersion,
    NotEvaluated,
    NotEvaluatedReason,
    RepoRunResult,
    RepoRunStatus,
    Run,
    RunStatus,
)
from hw_checker.domain.vcs import CommitInfo, PullRequestRef

__all__ = [
    "Assignment",
    "AssignmentResult",
    "AssignmentVersion",
    "CheckDescription",
    "CheckInput",
    "CheckParamDescription",
    "CommitInfo",
    "Evidence",
    "ExecutionRequest",
    "ExecutionResult",
    "FailureKind",
    "Finding",
    "FindingSource",
    "FixProposal",
    "GitHubRepo",
    "HttpMethod",
    "HttpProbe",
    "JudgedFinding",
    "NotEvaluated",
    "NotEvaluatedReason",
    "Pair",
    "ParamValue",
    "Patch",
    "ProbeResult",
    "ProposalStatus",
    "PullRequestRef",
    "RepoRunResult",
    "RepoRunStatus",
    "RepoTarget",
    "Requirement",
    "RequirementKind",
    "ReviewDecision",
    "ReviewOutcome",
    "RosterResult",
    "RosterRow",
    "RosterWarning",
    "RosterWarningKind",
    "Run",
    "RunStatus",
    "Severity",
    "StepStatus",
    "Student",
    "Verdict",
    "parse_github_url",
]
