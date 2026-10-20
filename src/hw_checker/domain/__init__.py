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
from hw_checker.domain.findings import (
    Evidence,
    Finding,
    FindingSource,
    JudgedFinding,
    Verdict,
)
from hw_checker.domain.roster import (
    Pair,
    RepoTarget,
    RosterRow,
    RosterWarning,
    RosterWarningKind,
    Student,
)

__all__ = [
    "Assignment",
    "Evidence",
    "Finding",
    "FindingSource",
    "HttpMethod",
    "HttpProbe",
    "JudgedFinding",
    "Pair",
    "RepoTarget",
    "Requirement",
    "RequirementKind",
    "RosterRow",
    "RosterWarning",
    "RosterWarningKind",
    "Severity",
    "Student",
    "Verdict",
]
