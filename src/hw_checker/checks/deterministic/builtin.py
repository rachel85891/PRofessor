"""The built-in registered checks, for bootstrap to put in the `CheckRegistry`."""

from hw_checker.checks.deterministic.naming import NAMING_CHECKS
from hw_checker.checks.deterministic.structure import file_exists, folder_exists, project_count
from hw_checker.checks.registry import CheckFunction


def builtin_checks() -> tuple[CheckFunction, ...]:
    """Every built-in registered check: the structure checks and the six naming checks."""
    return (file_exists, folder_exists, project_count, *NAMING_CHECKS)
