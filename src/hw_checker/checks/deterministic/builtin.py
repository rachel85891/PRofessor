"""The built-in registered checks, for bootstrap to put in the `CheckRegistry`."""

from hw_checker.checks.deterministic.architecture import di_started
from hw_checker.checks.deterministic.class_checks import CLASS_CHECKS
from hw_checker.checks.deterministic.client import (
    client_element_ids,
    client_fetch_routes,
    client_file_refs,
)
from hw_checker.checks.deterministic.data_files import data_files_in_repo
from hw_checker.checks.deterministic.layer_checks import LAYER_CHECKS
from hw_checker.checks.deterministic.method_checks import METHOD_CHECKS
from hw_checker.checks.deterministic.naming import NAMING_CHECKS
from hw_checker.checks.deterministic.pattern_checks import code_patterns
from hw_checker.checks.deterministic.repository import (
    gitignore_covers,
    no_absolute_paths,
    not_committed,
)
from hw_checker.checks.deterministic.structure import (
    file_exists,
    folder_exists,
    project_count,
)
from hw_checker.checks.registry import CheckFunction

REPOSITORY_CHECKS: tuple[CheckFunction, ...] = (
    gitignore_covers,
    not_committed,
    no_absolute_paths,
    data_files_in_repo,
)
CLIENT_CHECKS: tuple[CheckFunction, ...] = (
    client_file_refs,
    client_element_ids,
    client_fetch_routes,
)
ARCHITECTURE_CHECKS: tuple[CheckFunction, ...] = (
    di_started,
    *LAYER_CHECKS,
    code_patterns,
    *CLASS_CHECKS,
    *METHOD_CHECKS,
)


def builtin_checks() -> tuple[CheckFunction, ...]:
    """Every built-in registered check: the structure checks, the six naming checks, the
    repository checks (with the data files the run had to create), the client-file checks
    and the architecture-evidence checks (DI started, the layer and DI checks, the
    code-pattern check, the two class-placement checks, and the methods-are-async check)."""
    return (
        file_exists,
        folder_exists,
        project_count,
        *NAMING_CHECKS,
        *REPOSITORY_CHECKS,
        *CLIENT_CHECKS,
        *ARCHITECTURE_CHECKS,
    )
