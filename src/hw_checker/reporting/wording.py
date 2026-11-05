"""Plain-English wording for the reviewer's reports, kept in one place."""

from hw_checker.domain import (
    CheckedCommit,
    CommitChoice,
    FailureKind,
    NotEvaluatedReason,
    Severity,
)

SHORT_SHA = 7

NOT_EVALUATED_REASONS: dict[NotEvaluatedReason, str] = {
    NotEvaluatedReason.NO_EXECUTION: "The repo was not built or run, so this was not checked.",
    NotEvaluatedReason.BUILD_FAILED: (
        "The project did not build, so this could not be checked while it was running."
    ),
    NotEvaluatedReason.RUN_NOT_HEALTHY: (
        "The app did not start or did not answer, so this could not be checked over HTTP."
    ),
    NotEvaluatedReason.ENVIRONMENT_ISSUE: (
        "A problem on the checking machine, not in the student's code, kept this from "
        "being checked."
    ),
    NotEvaluatedReason.ROUTE_NOT_FOUND: (
        "No route in the student's code matches the request, so it was not sent; check "
        "the routes by hand."
    ),
    NotEvaluatedReason.BODY_MISMATCH: (
        "The request body did not fit the student's model, so the failure may not be the "
        "student's; check by hand."
    ),
    NotEvaluatedReason.DEPENDENCY_FAILED: (
        "The request needed a value (such as a new user's id) that an earlier request did "
        "not return, so it was not sent."
    ),
}
"""One sentence per reason a requirement was not checked."""

FAILURE_KINDS: dict[FailureKind, str] = {
    FailureKind.BUILD_FAILED: "The project does not build.",
    FailureKind.STARTUP_FAILED: "The app built but crashed or exited while starting.",
    FailureKind.DB_UNREACHABLE: "The app could not reach its database.",
    FailureKind.HARDCODED_CONNECTION_UNRESOLVED: (
        "The app uses a hard-coded connection that could not be adapted to run here."
    ),
    FailureKind.TIMEOUT: "A step took too long and was stopped.",
    FailureKind.ENVIRONMENT_ERROR: (
        "A problem on the checking machine, not in the student's code; run the repo again."
    ),
    FailureKind.PROJECT_NOT_FOUND: (
        "No single Web API project was found in the repo; flagged for review by hand."
    ),
}
"""One sentence per kind of build or run failure."""

SEVERITY_ORDER: tuple[Severity, ...] = (Severity.CRITICAL, Severity.MAJOR, Severity.MINOR)
"""The order of the findings' sections in a repo report: most serious first."""


def describe_checked_commit(checked: CheckedCommit) -> str:
    """One line on which commit was checked and why, for the report and the approval gate."""
    sha = f"`{checked.sha[:SHORT_SHA]}` ({checked.authored_at:%Y-%m-%d})"
    next_task = f"`{(checked.next_task_sha or '')[:SHORT_SHA]}`"
    match checked.choice:
        case CommitChoice.BEFORE_NEXT_TASK:
            later = "commit" if checked.later_commits == 1 else "commits"
            return (
                f"{sha}, the last before the next task started at {next_task}. The repo has "
                f"{checked.later_commits} later {later}; the pull request is based on this "
                "older commit."
            )
        case CommitChoice.NEXT_TASK_NOT_STARTED:
            return f"{sha}, the latest; the next task has not started."
        case CommitChoice.NEXT_TASK_AT_FIRST_COMMIT:
            return (
                f"{sha}, the latest: the repo's first commit ({next_task}) already looks like "
                "the next task, so the commit before it cannot be found. Check by hand which "
                "task this code is for."
            )
