"""Error isolation at a repo-level node boundary (design §7)."""

import logging
from collections.abc import Callable
from typing import Any

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
) -> Update:
    """Run `step` with run and repo ids in the log context, turning a failure into data.

    A failure is logged and returned as `{"errors": [<description>]}` (or `on_error`'s
    update), so the repo continues to `record_repo` and the round goes on.
    """
    with log_context(run_id=run_id, repo_id=repo_id):
        try:
            return step()
        except Exception as error:
            # Node boundary, the one place a general Exception is caught: a failure in one
            # repo must never stop the round. It is logged and kept in the repo's errors.
            message = describe_error(node, error)
            logger.error("Node %s failed: %s", node, message, exc_info=error)
            return on_error(message) if on_error else {"errors": [message]}
