"""Domain exception hierarchy (design §13).

Adapters map external library exceptions to these classes, so upper layers never
need to know which library failed.
"""


class HwCheckerError(Exception):
    """Base class for every expected error in HW Checker."""


class RosterError(HwCheckerError):
    """The roster file cannot be read or has an unusable structure."""


class RepoUnavailableError(HwCheckerError):
    """A student repo cannot be cloned, fetched or read."""


class GitOperationError(HwCheckerError):
    """A local Git operation (branch, commit) failed in an existing working copy."""


class BuildError(HwCheckerError):
    """A build could not be carried out for a reason that is not a student build failure."""


class ExecutionEnvironmentError(HwCheckerError):
    """The execution environment (Docker, SQL Server) is unavailable or misbehaving."""


class LLMError(HwCheckerError):
    """An LLM call failed, or its output could not be parsed into the expected model."""


class PublishError(HwCheckerError):
    """Pushing a branch or opening or updating a pull request failed."""


class InvalidAssignmentError(HwCheckerError):
    """Assignment data exists but is invalid (for example a malformed requirements file)."""


class StorageError(HwCheckerError):
    """Persisting or loading data (database rows or written files) failed."""


class NotFoundError(HwCheckerError):
    """A requested entity (run, proposal, assignment) does not exist."""
