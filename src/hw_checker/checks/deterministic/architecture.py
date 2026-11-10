"""Checks on the repo's local architecture evidence (`CheckInput.architecture`, stage 19).

The evidence is lexical: a signal is a candidate, and a missing one is not proof. These
checks only count signals; what the counts must reach is the assignment's data.
"""

from pydantic import BaseModel, ConfigDict, Field

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import DI_NOT_STARTED, DI_STARTED
from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignalKind,
    CheckInput,
    CheckPassed,
    EvidenceCoverageStatus,
    NotEvaluatedReason,
)


class DiStartedParams(BaseModel):
    """At least `min_registrations` registration signals and `min_interface_constructors`
    constructors with a parameter typed by a declared interface."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_registrations: int = Field(ge=0)
    min_interface_constructors: int = Field(ge=0)


@check(
    "di_started",
    DiStartedParams,
    "Dependency injection has started: enough service registrations and constructors "
    "taking an interface (counts of the architecture evidence's signals).",
    reads_architecture=True,
)
def di_started(check_input: CheckInput, params: DiStartedParams) -> CheckResult:
    """Passes, saying what it counted, when both counts are reached; not evaluated when the
    C# signals could not be read at all."""
    evidence = check_input.architecture
    if evidence is None or evidence.signal_coverage.status is EvidenceCoverageStatus.UNAVAILABLE:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    registrations = _registrations(evidence)
    constructors = _interface_constructors(evidence)
    if (
        registrations >= params.min_registrations
        and constructors >= params.min_interface_constructors
    ):
        message = DI_STARTED.render(registrations=registrations, constructors=constructors)
        return CheckPassed(message=message.en)
    message = DI_NOT_STARTED.render(
        registrations=registrations,
        min_registrations=params.min_registrations,
        constructors=constructors,
        min_constructors=params.min_interface_constructors,
    )
    return (deterministic_finding(check_input, message, [str(registrations), str(constructors)]),)


def _registrations(evidence: ArchitectureEvidence) -> int:
    return sum(
        signal.kind is ArchitectureSignalKind.REGISTRATION_CANDIDATE for signal in evidence.signals
    )


def _interface_constructors(evidence: ArchitectureEvidence) -> int:
    """Constructors with at least one interface parameter: the reader gives one signal per
    such parameter, on the constructor's line."""
    return len(
        {
            (signal.file_path, signal.line)
            for signal in evidence.signals
            if signal.kind is ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER
        }
    )
