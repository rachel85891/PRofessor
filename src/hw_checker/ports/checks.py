"""Port for the catalog of registered deterministic checks."""

from collections.abc import Mapping
from typing import Protocol

from hw_checker.domain import CheckDescription, ParamValue


class CheckCatalog(Protocol):
    """Knows which checks exist and which params they accept; runs nothing."""

    def check_names(self) -> frozenset[str]:
        """Return the names of all registered checks. Raises nothing."""
        ...

    def validate_params(self, check_name: str, params: Mapping[str, ParamValue]) -> tuple[str, ...]:
        """Return plain-language problems with `params` for `check_name`; empty if valid.

        An unknown check name is reported as a problem, not raised. Raises nothing.
        """
        ...

    def describe(self) -> tuple[CheckDescription, ...]:
        """Describe every registered check and its params, ordered by name. Raises nothing."""
        ...
