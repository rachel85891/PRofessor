"""Execution models: the outcome of building, running and probing a repo (design §5, §9)."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from hw_checker.domain.assignment import VARIABLE_USE, HttpMethod, HttpProbe
from hw_checker.domain.base import DomainModel, HttpStatusCode, Identifier, NonBlankStr

MAX_LOGS_EXCERPT_CHARS = 8000
"""Upper bound for a log excerpt kept in state and reports; full logs stay on disk (§7)."""

MAX_PROBE_ERROR_CHARS = 500
"""Upper bound for the short error text of a single probe."""

MAX_RESPONSE_EXCERPT_CHARS = 500
"""Upper bound for the start of a probe's response body kept for the reviewer."""

MAX_CAPTURED_CHARS = 500
"""Upper bound for a value captured from a response."""

MAX_PATH_TEXT_CHARS = 1000
"""Upper bound for a path text recorded from student code."""

MAX_ROUTE_TEXT_CHARS = 500
"""Upper bound for a route template or a routing description recorded from student code."""

MAX_BODY_CHARS = 10_000
"""Upper bound for a recorded probe body."""


class FailureKind(StrEnum):
    """Why a build or run failed (design §5). Choosing the kind is the runner's job."""

    BUILD_FAILED = "build_failed"
    STARTUP_FAILED = "startup_failed"
    DB_UNREACHABLE = "db_unreachable"
    HARDCODED_CONNECTION_UNRESOLVED = "hardcoded_connection_unresolved"
    TIMEOUT = "timeout"
    ENVIRONMENT_ERROR = "environment_error"
    PROJECT_NOT_FOUND = "project_not_found"
    """No .csproj, or not exactly one Web project; flagged for manual review."""

    @property
    def is_student_issue(self) -> bool:
        """True only for kinds that count against the student; the rest go to manual review."""
        return self in (FailureKind.BUILD_FAILED, FailureKind.STARTUP_FAILED)


class StepStatus(StrEnum):
    """Outcome of one execution step (prepare, build or run)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BodyFieldOutcome(StrEnum):
    """What happened to one field when a probe body was adapted to the student's model."""

    MATCHED = "matched"
    """The body key matched a property (ignoring case) and its value already fit."""
    MATCHED_BY_CONTAINS = "matched_by_contains"
    """No property has the key's name, but exactly one property's name contains it once
    case, `_`, `-` and digits are ignored (`password` for `LogInPassword`)."""
    CONVERTED = "converted"
    """The value was converted to the property's type, for example "12345" to 12345."""
    FILLED = "filled"
    """A required property the body did not mention got a type-appropriate dummy value."""
    UNMATCHED = "unmatched"
    """The body key matches no property of the model; it was sent as written."""
    NOT_CONVERTIBLE = "not_convertible"
    """The value cannot be converted to the property's type; a dummy value was sent."""


class BodyFieldNote(DomainModel):
    """One field of an adapted probe body.

    For a key with alternatives (`userName|email`), `alternative` is the one that matched
    `property_name`; an unmatched key lists in `candidates` the properties it could not
    choose between.
    """

    field: NonBlankStr
    """The body key, or for a filled field the property's name."""
    property_name: str | None = None
    outcome: BodyFieldOutcome
    alternative: str | None = None
    candidates: tuple[str, ...] = ()


MISMATCH_OUTCOMES = frozenset({BodyFieldOutcome.UNMATCHED, BodyFieldOutcome.NOT_CONVERTIBLE})


class BodyAdaptation(DomainModel):
    """How a probe's intended body was adapted to the model of the action it reached.

    `adapted` is False when no model could be read (`reason` says why); the body was then
    sent as written.
    """

    adapted: bool
    model: str | None = None
    reason: str = Field(default="", max_length=MAX_ROUTE_TEXT_CHARS)
    notes: tuple[BodyFieldNote, ...] = ()

    @property
    def mismatched(self) -> bool:
        """True when a body field matched no property or could not be converted."""
        return any(note.outcome in MISMATCH_OUTCOMES for note in self.notes)


class CaptureFailure(StrEnum):
    """Why a probe could not capture its value."""

    NOT_SENT = "not_sent"
    NO_RESPONSE = "no_response"
    NO_JSON = "no_json"
    """The response body is not a JSON object."""
    FIELD_MISSING = "field_missing"
    NOT_A_VALUE = "not_a_value"
    """The field holds null, an object, a list, or a text too long to use."""
    NO_LOCATION = "no_location"
    AMBIGUOUS = "ambiguous"
    """Several fields could hold the value (`userId` and `roleId` for `body:id`)."""
    NOT_AN_IDENTIFIER = "not_an_identifier"
    """A field found only by the "ends with" rule holds something other than an integer, a
    string of digits or a GUID (`isPaid: false` for `body:id`), so it is not taken as an id."""


class CaptureResult(DomainModel):
    """The value a probe captured for `{name}`, or why it could not.

    `source_field` is the response field the value came from (it may differ from the
    capture's field, as `userId` for `body:id`); `candidates` lists the fields of an
    ambiguous capture.
    """

    name: NonBlankStr
    value: str | None = Field(default=None, max_length=MAX_CAPTURED_CHARS)
    failure: CaptureFailure | None = None
    source_field: str | None = None
    candidates: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _value_or_failure(self) -> Self:
        if (self.value is None) == (self.failure is None):
            raise ValueError("a capture has either a value or a failure")
        return self


class ResponseShape(StrEnum):
    """What a probe's response body holds, as far as content checks are concerned."""

    OBJECT = "object"
    ARRAY_OF_OBJECTS = "array_of_objects"
    ARRAY_OF_SCALARS = "array_of_scalars"
    EMPTY_ARRAY = "empty_array"
    SCALAR = "scalar"
    NOT_JSON = "not_json"
    EMPTY = "empty"
    CUT = "cut"
    """The body reached the size limit, so its content cannot be judged."""


class RouteResolution(StrEnum):
    """How the path a probe was sent to was chosen from its candidate paths."""

    RESOLVED = "resolved"
    """Sent to the first candidate that matches a route declared for the probe's method."""
    ROUTE_NOT_FOUND = "route_not_found"
    """No candidate matches a declared route, so the probe was not sent."""
    UNAVAILABLE = "unavailable"
    """The route table was empty or incomplete, so the probe was sent to its first path."""
    DEPENDENCY_MISSING = "dependency_missing"
    """The probe uses a `{name}` that no earlier probe captured, so it was not sent."""


NOT_SENT_RESOLUTIONS = frozenset(
    {RouteResolution.ROUTE_NOT_FOUND, RouteResolution.DEPENDENCY_MISSING}
)


class ProbeResult(DomainModel):
    """The result of one HTTP probe against the running app.

    The probe carries the requirement and assignment it belongs to. `sent_path` is the
    candidate path the request went to (None when it was not sent, and for results
    recorded before path alternatives existed). `controller` is the class whose declared
    route the path matched (None when no route was used, and for older results).
    `sent_body` is the body that was actually sent, after `body_adaptation` adapted the
    probe's intended body to the student's model (both None for a probe without a body).
    `sent_path` and `sent_body` have captured `{name}` values filled in; `capture` is what
    this probe captured for later probes, or why it could not.
    """

    probe: HttpProbe
    actual_status: HttpStatusCode | None = None
    passed: bool
    error: str = Field(default="", max_length=MAX_PROBE_ERROR_CHARS)
    sent_path: str | None = None
    route_resolution: RouteResolution = RouteResolution.UNAVAILABLE
    controller: str | None = None
    sent_body: str | None = Field(default=None, max_length=MAX_BODY_CHARS)
    body_adaptation: BodyAdaptation | None = None
    response_excerpt: str = Field(default="", max_length=MAX_RESPONSE_EXCERPT_CHARS)
    """The start of the response body, for the reviewer; it may hold student data."""
    capture: CaptureResult | None = None
    response_shape: ResponseShape | None = None
    """The shape of the full response body; None when there was no answer."""
    missing_values: tuple[str, ...] = ()
    """The probe's expected values (variables filled in) that the response lacks."""

    @property
    def body_mismatched(self) -> bool:
        """True when the body sent had fields that do not fit the student's model."""
        return self.body_adaptation is not None and self.body_adaptation.mismatched

    @property
    def requirement_id(self) -> str:
        """The requirement this probe result belongs to."""
        return self.probe.requirement_id

    @property
    def assignment_id(self) -> str:
        """The assignment this probe result belongs to."""
        return self.probe.assignment_id

    @model_validator(mode="after")
    def _passed_matches_status(self) -> Self:
        expected_pass = self.actual_status in self.probe.allowed_statuses
        if self.passed != expected_pass:
            raise ValueError("passed must be true exactly when actual_status is an allowed status")
        return self

    @model_validator(mode="after")
    def _unsent_probe_has_no_answer(self) -> Self:
        if self.route_resolution in NOT_SENT_RESOLUTIONS and (
            self.sent_path is not None or self.actual_status is not None
        ):
            raise ValueError(
                "a probe whose route was not found or whose dependency is missing is never sent"
            )
        if self.sent_path is not None and not any(
            _fills_candidate(self.sent_path, candidate) for candidate in self.probe.candidate_paths
        ):
            raise ValueError("sent_path must be one of the probe's candidate paths")
        if self.controller is not None and self.route_resolution is not RouteResolution.RESOLVED:
            raise ValueError("only a probe resolved to a declared route has a controller")
        if self.sent_body is not None and self.route_resolution in NOT_SENT_RESOLUTIONS:
            raise ValueError("a probe that was not sent has no sent_body")
        return self


def _fills_candidate(sent_path: str, candidate: str) -> bool:
    """True when `sent_path` is `candidate` with its `{name}` variables filled in."""
    parts: list[str] = []
    last = 0
    for match in VARIABLE_USE.finditer(candidate):
        parts += [re.escape(candidate[last : match.start()]), "[^/?#]+"]
        last = match.end()
    parts.append(re.escape(candidate[last:]))
    return re.fullmatch("".join(parts), sent_path) is not None


class PathFixKind(StrEnum):
    """Why a file path in the student's code had to be adapted to run in the container."""

    ABSOLUTE = "absolute"
    """A hard-coded absolute host path (`C:\\...`, `\\\\server\\...`)."""
    CASE = "case"
    """A relative path whose letter case differs from the real file (Windows ignores case)."""


class PathNotRewrittenReason(StrEnum):
    """Why a detected path could not be rewritten safely."""

    NO_MATCH = "no_match"
    SEVERAL_MATCHES = "several_matches"
    DYNAMIC = "dynamic"
    """The path is built with interpolation, so there is no single literal to rewrite."""


class PathRewrite(DomainModel):
    """One path detected in the student's code, and what the runner did with it.

    The rewrite is made only in the runner's temporary copy (design §9, step 4); the
    student's working copy and repo are never changed, and fixes never include it.
    """

    file_path: NonBlankStr
    """The source file, relative to the working copy, with forward slashes."""
    line: int = Field(ge=1)
    kind: PathFixKind
    original: str = Field(min_length=1, max_length=MAX_PATH_TEXT_CHARS)
    rewritten: str | None = Field(default=None, max_length=MAX_PATH_TEXT_CHARS)
    """The project-relative path used instead, or None when the path was not rewritten."""
    not_rewritten_reason: PathNotRewrittenReason | None = None

    @model_validator(mode="after")
    def _rewritten_or_reason(self) -> Self:
        if (self.rewritten is None) == (self.not_rewritten_reason is None):
            raise ValueError("a path is either rewritten or has a not_rewritten_reason")
        return self


class DeclaredRoute(DomainModel):
    """One route a controller action declares in the student's code (attribute routing).

    `template` is the class and action templates combined, with `[controller]` and
    `[action]` replaced, without leading or trailing slashes, otherwise as written (for
    example `api/User/{id}`). `method` is None when the action has a `[Route]` but no HTTP
    method attribute, so it answers every method.
    """

    method: HttpMethod | None
    template: str = Field(max_length=MAX_ROUTE_TEXT_CHARS)
    controller: NonBlankStr
    action: NonBlankStr
    file_path: NonBlankStr
    line: int = Field(ge=1)


class UnsupportedRouting(DomainModel):
    """A routing form the route table cannot read, such as a minimal API (`app.MapGet`)."""

    file_path: NonBlankStr
    line: int = Field(ge=1)
    form: str = Field(min_length=1, max_length=MAX_ROUTE_TEXT_CHARS)


class RouteTable(DomainModel):
    """The routes declared in the student's code, read as text, for resolving probe paths.

    The table is usable only when it has routes and nothing unsupported was found: a route
    declared in a form the table cannot read would otherwise look missing.
    """

    routes: tuple[DeclaredRoute, ...] = ()
    unsupported: tuple[UnsupportedRouting, ...] = ()

    @property
    def usable(self) -> bool:
        """True when probe paths can be resolved against this table."""
        return bool(self.routes) and not self.unsupported


MAX_CODE_FACTS = 500
"""The most entries recorded in each list of `CodeFacts`."""


class TypeKind(StrEnum):
    """The kind of a C# type declaration."""

    CLASS = "class"
    RECORD = "record"
    STRUCT = "struct"
    INTERFACE = "interface"
    ENUM = "enum"


class TypeFact(DomainModel):
    """A type declared in the student's code."""

    name: NonBlankStr
    kind: TypeKind
    file_path: NonBlankStr
    line: int = Field(ge=1)
    is_controller: bool = False
    """It derives from `ControllerBase` or `Controller`."""


class MemberFact(DomainModel):
    """A public property, a method or a local variable declared in the student's code.

    `declaring_type` is None for a method or local variable outside any type (top-level
    statements in `Program.cs`).
    """

    name: NonBlankStr
    declaring_type: str | None = None
    file_path: NonBlankStr
    line: int = Field(ge=1)


class FileFact(DomainModel):
    """A source file and the types it declares, in order."""

    file_path: NonBlankStr
    types: tuple[str, ...] = ()


class CodeFacts(DomainModel):
    """Names declared in the student's C# code, read as text, for the naming checks.

    Generated, designer and migration files and names a student cannot choose (overrides,
    explicit interface implementations) are left out. Each list holds at most
    `MAX_CODE_FACTS` entries; `truncated` names the lists that were cut.
    """

    types: tuple[TypeFact, ...] = Field(default=(), max_length=MAX_CODE_FACTS)
    properties: tuple[MemberFact, ...] = Field(default=(), max_length=MAX_CODE_FACTS)
    methods: tuple[MemberFact, ...] = Field(default=(), max_length=MAX_CODE_FACTS)
    locals: tuple[MemberFact, ...] = Field(default=(), max_length=MAX_CODE_FACTS)
    files: tuple[FileFact, ...] = Field(default=(), max_length=MAX_CODE_FACTS)
    truncated: tuple[str, ...] = ()


class ExecutionResult(DomainModel):
    """Prepare, build and run outcome for one repo, with probe results and a log excerpt.

    `prepare` is project discovery and copying the code in; `build` is restore and publish.
    The steps run in that order, and the steps after a failed step are skipped.
    """

    prepare_status: StepStatus = StepStatus.SUCCEEDED
    build_status: StepStatus
    run_status: StepStatus
    failure_kind: FailureKind | None = None
    logs_excerpt: str = Field(default="", max_length=MAX_LOGS_EXCERPT_CHARS)
    logs_path: Path | None = None
    probe_results: tuple[ProbeResult, ...] = ()
    path_rewrites: tuple[PathRewrite, ...] = ()
    """Paths in the student's code that were adapted (or could not be) to run in Docker."""
    routes: RouteTable | None = None
    """The routes declared in the student's code; None when they were not read."""
    primary_controller: str | None = None
    """The controller probes were steered to: the one that could serve the most probes."""
    target_framework: str | None = None
    """The project's `<TargetFramework>` (for example `net8.0`); None when it was not read."""
    framework_outdated: bool = False
    """True when the project targets a .NET version older than the course's. The project is
    still built and run; the student is asked to move it, and it never lowers the grade."""
    serving_controllers: tuple[str, ...] = ()
    """Every controller that could serve at least one probe, sorted by name."""
    code_facts: CodeFacts | None = None
    """Names declared in the student's code, for the naming checks; None when not read."""

    @property
    def steps(self) -> tuple[StepStatus, StepStatus, StepStatus]:
        """The step statuses in execution order: prepare, build, run."""
        return (self.prepare_status, self.build_status, self.run_status)

    @property
    def has_unresolved_paths(self) -> bool:
        """True when a detected path could not be rewritten, so the run may fail for that."""
        return any(rewrite.rewritten is None for rewrite in self.path_rewrites)

    @model_validator(mode="after")
    def _failure_kind_matches_steps(self) -> Self:
        failed = StepStatus.FAILED in self.steps
        if failed and self.failure_kind is None:
            raise ValueError("a failed step needs a failure_kind")
        if not failed and self.failure_kind is not None:
            raise ValueError("failure_kind is only allowed when a step failed")
        if (
            self.failure_kind is FailureKind.PROJECT_NOT_FOUND
            and self.prepare_status is not StepStatus.FAILED
        ):
            raise ValueError("project_not_found requires a failed prepare step")
        return self

    @model_validator(mode="after")
    def _steps_after_a_failure_are_skipped(self) -> Self:
        if StepStatus.FAILED in self.steps:
            first_failed = self.steps.index(StepStatus.FAILED)
            if any(step is not StepStatus.SKIPPED for step in self.steps[first_failed + 1 :]):
                raise ValueError("steps after a failed step must be skipped")
        return self

    @model_validator(mode="after")
    def _primary_controller_can_serve(self) -> Self:
        if self.primary_controller is not None and (
            self.primary_controller not in self.serving_controllers
        ):
            raise ValueError("primary_controller must be one of serving_controllers")
        return self

    @model_validator(mode="after")
    def _probes_only_after_successful_run(self) -> Self:
        if self.probe_results and self.run_status is not StepStatus.SUCCEEDED:
            raise ValueError("probe_results require a successful run")
        return self


class ExecutionRequest(DomainModel):
    """Everything the runner needs to prepare, build, run and probe one repo."""

    run_id: Identifier
    repo_id: Identifier
    working_copy: Path
    probes: tuple[HttpProbe, ...] = ()
    environment: tuple[tuple[NonBlankStr, str], ...] = ()
    """Extra environment variables for the running app (for example connection strings)."""
    logs_dir: Path

    @model_validator(mode="after")
    def _environment_keys_are_unique(self) -> Self:
        keys = [key for key, _ in self.environment]
        if len(set(keys)) != len(keys):
            raise ValueError("environment keys must be unique")
        return self
