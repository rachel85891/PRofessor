"""Assignment models: assignments, requirements, HTTP probes, severity and kinds."""

import re
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    Field,
    PlainSerializer,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

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


ParamScalar = bool | int | float | str
ParamValue = ParamScalar | tuple[ParamScalar, ...]

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"

# Kinds whose requirements name a deterministic check; the other kinds must not.
KINDS_WITH_CHECK = frozenset({RequirementKind.STRUCTURE, RequirementKind.CUSTOM})


def _freeze_params(value: Mapping[str, ParamValue]) -> Mapping[str, ParamValue]:
    return MappingProxyType(dict(value))


CheckParams = Annotated[
    Mapping[str, ParamValue],
    AfterValidator(_freeze_params),
    PlainSerializer(dict, return_type=dict[str, ParamValue]),
]
"""Named check parameters, stored as a read-only mapping."""


ProbePath = Annotated[str, StringConstraints(pattern=r"^/")]
"""A request path; it starts with `/`."""

VARIABLE_NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
VARIABLE_USE = re.compile(r"\{(" + VARIABLE_NAME_PATTERN + r")\}")
"""`{name}` in a probe's path or body: a value captured by an earlier probe."""


class ProbeCapture(DomainModel):
    """A value a probe takes from its response, for later probes to use as `{name}`.

    `source` is `body:<field>` (a top-level field of a JSON response, matched ignoring case)
    or `location` (the last path segment of the `Location` header).
    """

    name: Annotated[str, StringConstraints(pattern=rf"^{VARIABLE_NAME_PATTERN}$")]
    source: Annotated[str, StringConstraints(pattern=rf"^(body:{VARIABLE_NAME_PATTERN}|location)$")]

    @property
    def body_field(self) -> str | None:
        """The response field for a `body:<field>` source; None for `location`."""
        return self.source.removeprefix("body:") if self.source.startswith("body:") else None


class HttpProbe(DomainModel):
    """One HTTP request against the running app and the statuses that count as a pass.

    `requirement_id` and `assignment_id` say which requirement the probe belongs to, so a
    `ProbeResult` (which carries its probe) can always be traced back to it.
    `path_alternatives` lists other paths a correct solution may use, because the task does
    not fix route names (`/api/users` or `/api/user`); the runner sends the probe to the
    first candidate that the student's code declares.
    """

    requirement_id: Identifier
    assignment_id: Identifier
    method: HttpMethod
    path: ProbePath
    path_alternatives: tuple[ProbePath, ...] = ()
    capture: ProbeCapture | None = None
    body: str | None = None
    expected_status: HttpStatusCode
    allowed_statuses: tuple[HttpStatusCode, ...] = Field(default=(), validate_default=True)
    notes: str = ""

    @property
    def candidate_paths(self) -> tuple[str, ...]:
        """`path` and then `path_alternatives`, in the order they are tried."""
        return (self.path, *self.path_alternatives)

    @property
    def variables_used(self) -> tuple[str, ...]:
        """The `{name}` variables in the paths and body, each once, in order of appearance."""
        found: list[str] = []
        for text in (*self.candidate_paths, self.body or ""):
            found += [name for name in VARIABLE_USE.findall(text) if name not in found]
        return tuple(found)

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
    """A single requirement of an assignment.

    `check` names the deterministic check for `structure` and `custom` requirements, and
    `params` holds its named parameters. `params` is a mapping because checks take named
    arguments of different kinds; it is read-only so the model stays immutable.
    """

    id: Identifier
    title: NonBlankStr
    description: str = ""
    severity: Severity
    kind: RequirementKind
    allowed_alternatives: tuple[NonBlankStr, ...] = ()
    check: Identifier | None = None
    params: CheckParams = Field(default_factory=lambda: MappingProxyType({}))
    http_probes: tuple[HttpProbe, ...] = ()

    @model_validator(mode="after")
    def _probes_match_kind(self) -> Self:
        if self.kind is RequirementKind.HTTP and not self.http_probes:
            raise ValueError("an http requirement needs at least one probe")
        if self.kind is not RequirementKind.HTTP and self.http_probes:
            raise ValueError(f"a {self.kind} requirement must not have http probes")
        return self

    @model_validator(mode="after")
    def _check_matches_kind(self) -> Self:
        if self.kind in KINDS_WITH_CHECK and self.check is None:
            raise ValueError(f"a {self.kind} requirement needs a check")
        if self.kind not in KINDS_WITH_CHECK and self.check is not None:
            raise ValueError(f"a {self.kind} requirement must not have a check")
        if self.check is None and self.params:
            raise ValueError("params are only allowed together with a check")
        return self

    @model_validator(mode="after")
    def _probes_belong_to_this_requirement(self) -> Self:
        if any(probe.requirement_id != self.id for probe in self.http_probes):
            raise ValueError(f"every probe must have requirement_id {self.id!r}")
        return self


class Assignment(DomainModel):
    """An assignment and its requirements. `_general` is the assignment with order 0."""

    id: Identifier
    order: int = Field(ge=0)
    title: NonBlankStr
    requirements: tuple[Requirement, ...]
    taught_techniques: tuple[NonBlankStr, ...] = ()
    lenient_notes: tuple[NonBlankStr, ...] = ()
    source_hash: str | None = Field(default=None, pattern=SHA256_HEX_PATTERN)
    """SHA-256 of the requirements file, so results show which version they were checked against."""

    @model_validator(mode="after")
    def _requirement_ids_are_unique(self) -> Self:
        ids = [requirement.id for requirement in self.requirements]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate requirement ids: {', '.join(duplicates)}")
        return self

    @model_validator(mode="after")
    def _probes_belong_to_this_assignment(self) -> Self:
        probes = (probe for requirement in self.requirements for probe in requirement.http_probes)
        if any(probe.assignment_id != self.id for probe in probes):
            raise ValueError(f"every probe must have assignment_id {self.id!r}")
        return self
