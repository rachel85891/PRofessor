"""The built-in registered checks, for bootstrap to put in the `CheckRegistry`."""

from hw_checker.checks.deterministic.naming import NAMING_CHECKS
from hw_checker.checks.deterministic.repository import (
    gitignore_covers,
    no_absolute_paths,
    not_committed,
)
from hw_checker.checks.deterministic.structure import file_exists, folder_exists
from hw_checker.checks.registry import CheckFunction

REPOSITORY_CHECKS: tuple[CheckFunction, ...] = (gitignore_covers, not_committed, no_absolute_paths)


def builtin_checks() -> tuple[CheckFunction, ...]:
    """Every built-in registered check: the structure checks, the six naming checks and
    the repository checks."""
    return (file_exists, folder_exists, *NAMING_CHECKS, *REPOSITORY_CHECKS)
