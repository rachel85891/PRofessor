"""Maps the outcome of one runner step to a `FailureKind` (ADR 0015, answer 9).

Total: every outcome gives a kind, or None for success. Anything unexpected becomes
`environment_error`, so it goes to manual review instead of counting against the student.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from hw_checker.domain import FailureKind

OOM_EXIT_CODE = 137
"""128 + SIGKILL: what a container killed for exceeding its memory limit exits with."""

# NuGet could not reach a package source: an environment problem.
SOURCE_UNREACHABLE_CODES = frozenset({"NU1301", "NU1801"})
# The project asks for a package or version that does not exist: the student's problem.
PACKAGE_NOT_FOUND_CODES = frozenset({"NU1100", "NU1101", "NU1102", "NU1103"})

_NUGET_ERROR = re.compile(r"\berror (NU\d{4})\b")


class RunnerStep(StrEnum):
    """The runner's steps, in order. Restore and build together make up the build status."""

    PREPARE = "prepare"
    RESTORE = "restore"
    BUILD = "build"
    RUN = "run"


@dataclass(frozen=True)
class StepOutcome:
    """What happened in one step.

    `exit_code` is None when the container never exited (still running, or never started).
    For `run`, `ready` says whether the app answered before the readiness timeout, and
    `unresolved_host` that the probe sidecar could not resolve the app's name at all. For
    `prepare`, `project_found` says whether discovery found exactly one Web project.
    """

    step: RunnerStep
    exit_code: int | None = None
    timed_out: bool = False
    oom_killed: bool = False
    engine_error: bool = False
    log_text: str = ""
    ready: bool = False
    project_found: bool = True
    unresolved_host: bool = False


def classify(outcome: StepOutcome) -> FailureKind | None:
    """Return the failure kind of `outcome`, or None if the step succeeded."""
    if outcome.engine_error or outcome.oom_killed or outcome.exit_code == OOM_EXIT_CODE:
        return FailureKind.ENVIRONMENT_ERROR
    match outcome.step:
        case RunnerStep.PREPARE:
            return _classify_prepare(outcome)
        case RunnerStep.RESTORE:
            return _classify_restore(outcome)
        case RunnerStep.BUILD:
            return _classify_build(outcome)
        case RunnerStep.RUN:
            return _classify_run(outcome)


def nuget_error_codes(log_text: str) -> frozenset[str]:
    """Return the NuGet error codes (`error NUxxxx`) found in a log."""
    return frozenset(_NUGET_ERROR.findall(log_text))


def _classify_prepare(outcome: StepOutcome) -> FailureKind | None:
    if not outcome.project_found:
        return FailureKind.PROJECT_NOT_FOUND
    if outcome.timed_out or outcome.exit_code not in (None, 0):
        return FailureKind.ENVIRONMENT_ERROR  # copying the code in failed
    return None


def _classify_restore(outcome: StepOutcome) -> FailureKind | None:
    if outcome.timed_out:
        return FailureKind.ENVIRONMENT_ERROR
    if outcome.exit_code == 0:
        return None
    codes = nuget_error_codes(outcome.log_text)
    if codes & SOURCE_UNREACHABLE_CODES:
        return FailureKind.ENVIRONMENT_ERROR
    if codes and codes <= PACKAGE_NOT_FOUND_CODES:
        return FailureKind.BUILD_FAILED
    return FailureKind.ENVIRONMENT_ERROR  # an unknown restore failure goes to manual review


def _classify_build(outcome: StepOutcome) -> FailureKind | None:
    if outcome.timed_out:
        return FailureKind.TIMEOUT
    if outcome.exit_code == 0:
        return None
    if outcome.exit_code is None:
        return FailureKind.ENVIRONMENT_ERROR  # the build container never finished
    return FailureKind.BUILD_FAILED


def _classify_run(outcome: StepOutcome) -> FailureKind | None:
    if outcome.ready:
        return None
    if outcome.unresolved_host:
        return FailureKind.ENVIRONMENT_ERROR  # our network, not the student's app
    if outcome.exit_code is not None:
        return FailureKind.STARTUP_FAILED  # exited before it answered
    if outcome.timed_out:
        return FailureKind.TIMEOUT  # still running, never answered
    return FailureKind.ENVIRONMENT_ERROR  # neither answered, exited nor timed out
