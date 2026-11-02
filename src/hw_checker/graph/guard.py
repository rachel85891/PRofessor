"""Error isolation at a repo-level node boundary (design §7)."""

import logging
from collections.abc import Callable
from typing import Any

from langgraph.runtime import get_runtime

from hw_checker.config.logging_setup import log_context
from hw_checker.domain.errors import HwCheckerError

logger = logging.getLogger(__name__)

Update = dict[str, Any]
"""A partial state update returned by a node."""


def describe_error(node: str, error: Exception) -> str:
    """One line for a repo's `errors`: the node, then the domain message or the error type."""
    if isinstance(error, HwCheckerError) and str(error).strip():
        return f"{node}: {error}"
    detail = str(error).strip()
    return f"{node}: unexpected {type(error).__name__}" + (f": {detail}" if detail else "")


def guarded(
    node: str,
    run_id: str,
    repo_id: str,
    step: Callable[[], Update],
    on_error: Callable[[str], Update] | None = None,
    retry_on: Callable[[Exception], bool] | None = None,
    max_attempts: int = 1,
) -> Update:
    """Run `step` with run and repo ids in the log context, turning a failure into data.

    A failure is logged and returned as `{"errors": [<description>]}` (or `on_error`'s
    update), so the repo continues to `record_repo` and the round goes on.

    With `retry_on`, a matching failure is raised again while the node has attempts left
    (out of `max_attempts`), so the node's `RetryPolicy` (with the same limit) runs it again;
    the last attempt's failure is recorded like any other, so it is recorded exactly once.
    """
    with log_context(run_id=run_id, repo_id=repo_id):
        try:
            return step()
        except Exception as error:
            # Node boundary, the one place a general Exception is caught: a failure in one
            # repo must never stop the round. It is logged and kept in the repo's errors.
            attempt = node_attempt()
            if retry_on is not None and retry_on(error) and attempt < max_attempts:
                logger.warning(
                    "Node %s failed on attempt %d of %d; it will run again: %s",
                    node,
                    attempt,
                    max_attempts,
                    describe_error(node, error),
                )
                raise
            message = describe_error(node, error)
            logger.error("Node %s failed: %s", node, message, exc_info=error)
            return on_error(message) if on_error else {"errors": [message]}


def node_attempt() -> int:
    """The running node's attempt number under its RetryPolicy, from 1."""
    runtime = get_runtime()
    info = runtime.execution_info if runtime is not None else None
    return info.node_attempt if info is not None else 1
