"""`di_started` on synthetic architecture evidence (stage 20)."""

from pathlib import Path

import pytest

from hw_checker.checks.deterministic.architecture import DiStartedParams, di_started
from hw_checker.checks.registry import CheckResult
from hw_checker.domain import (
    ArchitectureEvidence,
    ArchitectureSignal,
    ArchitectureSignalKind,
    Assignment,
    CheckInput,
    CheckPassed,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    ServiceLifetime,
    Severity,
)

REQUIREMENT = Requirement(
    id="hw03-di",
    title="DI has started",
    severity=Severity.MAJOR,
    kind=RequirementKind.STRUCTURE,
    check="di_started",
    params={"min_registrations": 2, "min_interface_constructors": 1},
)
ASSIGNMENT = Assignment(id="hw03", order=3, title="DI", requirements=(REQUIREMENT,))
THRESHOLDS = DiStartedParams(min_registrations=2, min_interface_constructors=1)


def registration(line: int) -> ArchitectureSignal:
    return ArchitectureSignal(
        kind=ArchitectureSignalKind.REGISTRATION_CANDIDATE,
        file_path="Api/Program.cs",
        line=line,
        subject="IUserService",
        related_subject="UserService",
        registration_method="AddScoped",
        lifetime=ServiceLifetime.SCOPED,
    )


def interface_parameter(file_path: str, line: int, subject: str) -> ArchitectureSignal:
    return ArchitectureSignal(
        kind=ArchitectureSignalKind.CONSTRUCTOR_INTERFACE_PARAMETER,
        file_path=file_path,
        line=line,
        subject=subject,
        related_subject="UsersController",
    )


def evidence(
    *signals: ArchitectureSignal,
    status: EvidenceCoverageStatus = EvidenceCoverageStatus.COMPLETE,
) -> ArchitectureEvidence:
    coverage = EvidenceCoverage(status=status, files_examined=3)
    return ArchitectureEvidence(
        signals=signals, inventory_coverage=coverage, signal_coverage=coverage
    )


def run(found: ArchitectureEvidence | None, params: DiStartedParams = THRESHOLDS) -> CheckResult:
    check_input = CheckInput(
        repo_dir=Path("repo"), assignment=ASSIGNMENT, requirement=REQUIREMENT, architecture=found
    )
    return di_started(check_input, params)


# One constructor with two interface parameters (two signals on its line) counts once.
STARTED = evidence(
    registration(3),
    registration(4),
    interface_parameter("Api/Controllers/UsersController.cs", 8, "IUserService"),
    interface_parameter("Api/Controllers/UsersController.cs", 8, "ILogger"),
)


def test_di_has_started_when_both_counts_are_reached() -> None:
    assert run(STARTED) == CheckPassed(
        message="DI has started (2 registrations, 1 constructors taking an interface)."
    )


def test_di_has_not_started_below_a_threshold_from_the_data() -> None:
    stricter = DiStartedParams(min_registrations=2, min_interface_constructors=2)

    outcome = run(STARTED, stricter)

    assert isinstance(outcome, tuple)
    [finding] = outcome
    assert finding.requirement_id == "hw03-di"
    assert finding.message_en == (
        "DI has not started: 2 registrations (at least 2) and 1 constructors taking an "
        "interface (at least 2)."
    )


def test_partial_coverage_is_still_counted() -> None:
    partial = STARTED.model_copy(
        update={
            "signal_coverage": EvidenceCoverage(
                status=EvidenceCoverageStatus.PARTIAL, files_examined=3
            )
        }
    )

    assert isinstance(run(partial), CheckPassed)


@pytest.mark.parametrize(
    "found", [None, evidence(status=EvidenceCoverageStatus.UNAVAILABLE)], ids=["unread", "none"]
)
def test_without_signals_it_is_not_evaluated(found: ArchitectureEvidence | None) -> None:
    assert run(found) is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_the_thresholds_are_required_data() -> None:
    with pytest.raises(ValueError, match="min_interface_constructors"):
        DiStartedParams.model_validate({"min_registrations": 1})
