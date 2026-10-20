"""Assignment models: assignments, requirements, HTTP probes, severity and kinds."""

from enum import StrEnum
from typing import Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from hw_checker.domain.base import DomainModel, HttpStatusCode, Identifier, NonBlankStr


class Severity(StrEnum):
    """How serious a finding is. Ordered: minor < major < critical."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Position in the severity order; higher is more serious."""
        return tuple(Severity).index(self)

    def __lt__(self, other: str) -> bool:
        return self.rank < _severity_rank(other)

    def __le__(self, other: str) -> bool:
        return self.rank <= _severity_rank(other)

    def __gt__(self, other: str) -> bool:
        return self.rank > _severity_rank(other)

    def __ge__(self, other: str) -> bool:
        return self.rank >= _severity_rank(other)


def _severity_rank(other: str) -> int:
    # Raise instead of returning NotImplemented: str would fall back to alphabetical order.
    if not isinstance(other, Severity):
        raise TypeError(f"cannot compare Severity with {type(other).__name__}")
    return other.rank


class RequirementKind(StrEnum):
    """How a requirement is checked (design §8)."""

    STRUCTURE = "structure"
    CODE = "code"
    HTTP = "http"
    BUILD = "build"
    CUSTOM = "custom"


class HttpMethod(StrEnum):
    """HTTP methods an HTTP probe may use."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class HttpProbe(DomainModel):
    """One HTTP request against the running app and the statuses that count as a pass."""

    method: HttpMethod
    path: str = Field(pattern=r"^/")
    body: str | None = None
    expected_status: HttpStatusCode
    allowed_statuses: tuple[HttpStatusCode, ...] = Field(default=(), validate_default=True)
    notes: str = ""

    @field_validator("allowed_statuses")
    @classmethod
    def _default_and_include_expected(
        cls, value: tuple[int, ...], info: ValidationInfo
    ) -> tuple[int, ...]:
        expected = info.data.get("expected_status")
        if expected is None:
            return value
        if not value:
            return (expected,)
        if expected not in value:
            raise ValueError(f"must include expected_status {expected}")
        return value


class Requirement(DomainModel):
    """A single requirement of an assignment."""

    id: Identifier
    title: NonBlankStr
    description: str = ""
    severity: Severity
    kind: RequirementKind
    allowed_alternatives: tuple[NonBlankStr, ...] = ()
    http_probes: tuple[HttpProbe, ...] = ()

    @model_validator(mode="after")
    def _probes_match_kind(self) -> Self:
        if self.kind is RequirementKind.HTTP and not self.http_probes:
            raise ValueError("an http requirement needs at least one probe")
        if self.kind is not RequirementKind.HTTP and self.http_probes:
            raise ValueError(f"a {self.kind} requirement must not have http probes")
        return self


class Assignment(DomainModel):
    """An assignment and its requirements. `_general` is the assignment with order 0."""

    id: Identifier
    order: int = Field(ge=0)
    title: NonBlankStr
    requirements: tuple[Requirement, ...]
    taught_techniques: tuple[NonBlankStr, ...] = ()
    lenient_notes: tuple[NonBlankStr, ...] = ()

    @model_validator(mode="after")
    def _requirement_ids_are_unique(self) -> Self:
        ids = [requirement.id for requirement in self.requirements]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate requirement ids: {', '.join(duplicates)}")
        return self
