"""The built-in registered checks, for bootstrap to put in the `CheckRegistry`."""

from hw_checker.checks.deterministic.structure import file_exists, folder_exists
from hw_checker.checks.registry import CheckFunction


def builtin_checks() -> tuple[CheckFunction, ...]:
    """Every built-in registered check: `file_exists` and `folder_exists`."""
    return (file_exists, folder_exists)
