"""Descriptions of the registered deterministic checks, for listing and editing forms."""

from hw_checker.domain.assignment import ParamValue
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr


class CheckParamDescription(DomainModel):
    """One parameter of a check: its name, a plain type name, and whether it is required."""

    name: Identifier
    type: NonBlankStr
    required: bool
    default: ParamValue | None = None


class CheckDescription(DomainModel):
    """A registered check and the parameters it takes."""

    name: Identifier
    description: NonBlankStr
    params: tuple[CheckParamDescription, ...] = ()
