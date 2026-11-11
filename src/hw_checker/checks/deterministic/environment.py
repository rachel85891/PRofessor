"""The detail of an `environment_issue`: which step failed, and why (stage 22).

The build and http evaluators read the execution result and say what kept a requirement
from being checked, so the reviewer never sees the reason alone. A path is shown by its
last parts only (the start of a student's full path names their own machine), with why it
could not be adapted.
"""

import re

from hw_checker.checks.deterministic.messages import (
    ENVIRONMENT_BUILD,
    ENVIRONMENT_MORE_PATHS,
    ENVIRONMENT_NO_MESSAGE,
    ENVIRONMENT_NOT_PROBED,
    ENVIRONMENT_PATH,
    ENVIRONMENT_PATH_WHY,
    ENVIRONMENT_PREPARE,
    ENVIRONMENT_PROBE_PATHS,
    ENVIRONMENT_RUN,
    ENVIRONMENT_RUN_PATHS,
)
from hw_checker.domain import ExecutionResult, NotEvaluatedOutcome, NotEvaluatedReason
from hw_checker.domain.run import MAX_NOT_EVALUATED_DETAIL_CHARS

MAX_PATHS_SHOWN = 2
TAIL_PARTS = 2
"""How many trailing parts of a path are shown, after `…`."""
MAX_MESSAGE_CHARS = 200
_SEPARATOR = re.compile(r"[\\/]+")


def environment_issue(detail: str) -> NotEvaluatedOutcome:
    """`environment_issue` with `detail`, cut to what a not-evaluated entry holds."""
    if len(detail) > MAX_NOT_EVALUATED_DETAIL_CHARS:
        detail = detail[: MAX_NOT_EVALUATED_DETAIL_CHARS - 1] + "…"
    return NotEvaluatedOutcome(reason=NotEvaluatedReason.ENVIRONMENT_ISSUE, detail=detail)


def prepare_failed(execution: ExecutionResult) -> NotEvaluatedOutcome:
    """The project was not prepared (no single Web project, an unsupported framework, a
    repo too large, or the code could not be copied in)."""
    return environment_issue(ENVIRONMENT_PREPARE.format(message=_first_line(execution)))


def build_failed(execution: ExecutionResult) -> NotEvaluatedOutcome:
    """The build failed for a reason that is not the student's (a timeout, the engine)."""
    kind = execution.failure_kind.value if execution.failure_kind else "unknown"
    return environment_issue(ENVIRONMENT_BUILD.format(kind=kind, message=_first_line(execution)))


def run_failed(execution: ExecutionResult) -> NotEvaluatedOutcome:
    """The app did not start or answer: with the hard-coded paths that could not be adapted
    when there are some, otherwise the run's own reason (a timeout, the engine)."""
    kind = execution.failure_kind.value if execution.failure_kind else "unknown"
    if execution.has_unresolved_paths:
        return environment_issue(ENVIRONMENT_RUN_PATHS.format(kind=kind, paths=_paths(execution)))
    return environment_issue(ENVIRONMENT_RUN.format(kind=kind, message=_first_line(execution)))


def probe_failed_with_paths(execution: ExecutionResult) -> NotEvaluatedOutcome:
    """A request failed while a hard-coded path could not be adapted."""
    return environment_issue(ENVIRONMENT_PROBE_PATHS.format(paths=_paths(execution)))


def not_probed(execution: ExecutionResult) -> NotEvaluatedOutcome:
    """The app answered, but no request of the requirement was sent."""
    return environment_issue(ENVIRONMENT_NOT_PROBED.format(count=len(execution.probe_results)))


def path_tail(path: str) -> str:
    """The last `TAIL_PARTS` parts of `path`, after `…` when there were more."""
    parts = [part for part in _SEPARATOR.split(path) if part]
    separator = "\\" if "\\" in path else "/"
    tail = separator.join(parts[-TAIL_PARTS:])
    return tail if len(parts) <= TAIL_PARTS else f"…{separator}{tail}"


def _paths(execution: ExecutionResult) -> str:
    unresolved = [rewrite for rewrite in execution.path_rewrites if rewrite.rewritten is None]
    shown = [
        ENVIRONMENT_PATH.format(
            tail=path_tail(rewrite.original),
            why=ENVIRONMENT_PATH_WHY.get(str(rewrite.not_rewritten_reason), "unknown"),
        )
        for rewrite in unresolved[:MAX_PATHS_SHOWN]
    ]
    more = len(unresolved) - len(shown)
    return ", ".join(shown) + (ENVIRONMENT_MORE_PATHS.format(count=more) if more else "")


def _first_line(execution: ExecutionResult) -> str:
    for line in execution.logs_excerpt.splitlines():
        if line.strip():
            return line.strip()[:MAX_MESSAGE_CHARS]
    return ENVIRONMENT_NO_MESSAGE
