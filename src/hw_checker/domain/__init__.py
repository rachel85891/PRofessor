"""Domain layer: pure models and business rules (repo, pair, requirement, finding).

May depend on: the standard library and Pydantic only.
"""

from hw_checker.domain.assignment import (
    Assignment,
    HttpMethod,
    HttpProbe,
    Requirement,
    RequirementKind,
    Severity,
)
from hw_checker.domain.execution import (
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
    Pair,
    RepoTarget,
    RosterRow,
    RosterWarning,
    RosterWarningKind,
    Student,
)
from hw_checker.domain.run import (
    AssignmentResult,
    RepoRunResult,
    RepoRunStatus,
    Run,
    RunStatus,
)

__all__ = [
    "Assignment",
    "AssignmentResult",
    "Evidence",
    "ExecutionResult",
    "FailureKind",
    "Finding",
    "FindingSource",
    "FixProposal",
    "HttpMethod",
    "HttpProbe",
    "JudgedFinding",
    "Pair",
    "Patch",
    "ProbeResult",
    "ProposalStatus",
    "RepoRunResult",
    "RepoRunStatus",
    "RepoTarget",
    "Requirement",
    "RequirementKind",
    "ReviewDecision",
    "ReviewOutcome",
    "RosterRow",
    "RosterWarning",
    "RosterWarningKind",
    "Run",
    "RunStatus",
    "Severity",
    "StepStatus",
    "Student",
    "Verdict",
]
