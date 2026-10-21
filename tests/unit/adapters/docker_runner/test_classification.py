import itertools

import pytest

from hw_checker.adapters.docker_runner.classification import (
    RunnerStep,
    StepOutcome,
    classify,
    nuget_error_codes,
)
from hw_checker.domain import FailureKind

# Log lines observed in the stage 5a spike, and NuGet's documented messages.
WORKLOAD_WARNING = (
    'An issue was encountered verifying workloads. For more information, run "dotnet workload '
    'update".'
)
BUILD_ERROR = (
    "/work/src/Controllers/ProductsController.cs(19,19): error CS0103: The name 'produts' "
    "does not exist in the current context [/work/src/SampleWebApi.csproj]"
)
STARTUP_CRASH = (
    "Unhandled exception. System.InvalidOperationException: startup failure for the spike\n"
    "   at Program.<Main>$(String[] args) in /work/src/Program.cs:line 23"
)
SOURCE_UNREACHABLE = (
    "/work/src/SampleWebApi.csproj : error NU1301: Unable to load the service index for "
    "source https://api.nuget.org/v3/index.json."
)
PACKAGE_NOT_FOUND = (
    "/work/src/SampleWebApi.csproj : error NU1101: Unable to find package "
    "Microsoft.AspNetCore.OpenApii. No packages exist with this id in source(s): nuget.org"
)
VERSION_NOT_FOUND = (
    "/work/src/SampleWebApi.csproj : error NU1102: Unable to find package "
    "Microsoft.AspNetCore.OpenApi with version (>= 99.0.0)"
)


def restore(exit_code: int | None, log: str = "", **flags: bool) -> StepOutcome:
    return StepOutcome(RunnerStep.RESTORE, exit_code=exit_code, log_text=log, **flags)


def test_successful_steps_have_no_failure_kind() -> None:
    assert classify(StepOutcome(RunnerStep.PREPARE)) is None
    assert classify(restore(0, WORKLOAD_WARNING)) is None
    assert classify(StepOutcome(RunnerStep.BUILD, exit_code=0, log_text=WORKLOAD_WARNING)) is None
    assert classify(StepOutcome(RunnerStep.RUN, ready=True)) is None


def test_discovery_failure_is_project_not_found() -> None:
    outcome = StepOutcome(RunnerStep.PREPARE, project_found=False)

    assert classify(outcome) is FailureKind.PROJECT_NOT_FOUND


def test_failed_copy_in_is_an_environment_error() -> None:
    assert classify(StepOutcome(RunnerStep.PREPARE, exit_code=1)) is FailureKind.ENVIRONMENT_ERROR


def test_restore_with_unreachable_source_is_an_environment_error() -> None:
    assert classify(restore(1, SOURCE_UNREACHABLE)) is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("log", [PACKAGE_NOT_FOUND, VERSION_NOT_FOUND])
def test_restore_with_missing_package_is_a_build_failure(log: str) -> None:
    assert classify(restore(1, f"{WORKLOAD_WARNING}\n{log}")) is FailureKind.BUILD_FAILED


def test_unreachable_source_wins_over_missing_package() -> None:
    log = f"{SOURCE_UNREACHABLE}\n{PACKAGE_NOT_FOUND}"

    assert classify(restore(1, log)) is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize(
    "log",
    [
        "",
        WORKLOAD_WARNING,
        "error MSB4025: The project file could not be loaded.",
        "error NU1605: Detected package downgrade",
        f"{PACKAGE_NOT_FOUND}\nerror NU1605: Detected package downgrade",
    ],
)
def test_unknown_restore_failure_is_an_environment_error(log: str) -> None:
    assert classify(restore(1, log)) is FailureKind.ENVIRONMENT_ERROR


def test_restore_timeout_is_an_environment_error() -> None:
    assert classify(restore(None, timed_out=True)) is FailureKind.ENVIRONMENT_ERROR


def test_non_zero_publish_is_a_build_failure() -> None:
    outcome = StepOutcome(
        RunnerStep.BUILD, exit_code=1, log_text=f"{WORKLOAD_WARNING}\n{BUILD_ERROR}"
    )

    assert classify(outcome) is FailureKind.BUILD_FAILED


def test_build_timeout_is_a_timeout() -> None:
    assert classify(StepOutcome(RunnerStep.BUILD, timed_out=True)) is FailureKind.TIMEOUT


def test_build_that_never_finished_is_an_environment_error() -> None:
    assert classify(StepOutcome(RunnerStep.BUILD)) is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("exit_code", [139, 1, 0])
def test_app_exiting_before_readiness_is_a_startup_failure(exit_code: int) -> None:
    outcome = StepOutcome(RunnerStep.RUN, exit_code=exit_code, log_text=STARTUP_CRASH)

    assert classify(outcome) is FailureKind.STARTUP_FAILED


def test_app_still_running_at_readiness_timeout_is_a_timeout() -> None:
    assert classify(StepOutcome(RunnerStep.RUN, timed_out=True)) is FailureKind.TIMEOUT


def test_run_that_neither_answered_nor_exited_is_an_environment_error() -> None:
    assert classify(StepOutcome(RunnerStep.RUN)) is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("step", list(RunnerStep))
def test_oom_kill_in_any_step_is_an_environment_error(step: RunnerStep) -> None:
    assert classify(StepOutcome(step, oom_killed=True)) is FailureKind.ENVIRONMENT_ERROR
    assert classify(StepOutcome(step, exit_code=137)) is FailureKind.ENVIRONMENT_ERROR


@pytest.mark.parametrize("step", list(RunnerStep))
def test_engine_error_in_any_step_is_an_environment_error(step: RunnerStep) -> None:
    outcome = StepOutcome(step, exit_code=0, ready=True, engine_error=True)

    assert classify(outcome) is FailureKind.ENVIRONMENT_ERROR


def test_classification_is_total() -> None:
    logs = ["", BUILD_ERROR, SOURCE_UNREACHABLE, PACKAGE_NOT_FOUND]
    for step, exit_code, timed_out, oom, engine, log, ready, found in itertools.product(
        RunnerStep,
        [None, 0, 1, 137, 139],
        [False, True],
        [False, True],
        [False, True],
        logs,
        [False, True],
        [False, True],
    ):
        outcome = StepOutcome(step, exit_code, timed_out, oom, engine, log, ready, found)
        result = classify(outcome)
        assert result is None or isinstance(result, FailureKind)


def test_nuget_error_codes_are_extracted() -> None:
    assert nuget_error_codes(f"{SOURCE_UNREACHABLE}\n{PACKAGE_NOT_FOUND}\nwarning NU1603") == {
        "NU1301",
        "NU1101",
    }
