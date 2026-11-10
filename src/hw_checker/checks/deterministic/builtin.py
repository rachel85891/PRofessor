"""The built-in registered checks, for bootstrap to put in the `CheckRegistry`."""

from hw_checker.checks.deterministic.architecture import di_started
from hw_checker.checks.deterministic.client import (
    client_element_ids,
    client_fetch_routes,
    client_file_refs,
)
from hw_checker.checks.deterministic.layer_checks import LAYER_CHECKS
from hw_checker.checks.deterministic.naming import NAMING_CHECKS
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

REPOSITORY_CHECKS: tuple[CheckFunction, ...] = (gitignore_covers, not_committed, no_absolute_paths)
CLIENT_CHECKS: tuple[CheckFunction, ...] = (
    client_file_refs,
    client_element_ids,
    client_fetch_routes,
)
ARCHITECTURE_CHECKS: tuple[CheckFunction, ...] = (di_started, *LAYER_CHECKS)


def builtin_checks() -> tuple[CheckFunction, ...]:
    """Every built-in registered check: the structure checks, the six naming checks, the
    repository checks, the client-file checks and the architecture-evidence checks (DI
    started, and the layer and DI checks)."""
    return (
        file_exists,
        folder_exists,
        project_count,
        *NAMING_CHECKS,
        *REPOSITORY_CHECKS,
        *CLIENT_CHECKS,
        *ARCHITECTURE_CHECKS,
    )
