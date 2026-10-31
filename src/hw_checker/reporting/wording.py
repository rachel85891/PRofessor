"""Plain-English wording for the reviewer's reports, kept in one place."""

from hw_checker.domain import FailureKind, NotEvaluatedReason, Severity

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
