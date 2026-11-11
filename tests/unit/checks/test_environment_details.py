"""Every `environment_issue` of the http and build evaluators has a detail (stage 22)."""

import pytest

from hw_checker.checks.deterministic.build import evaluate_build
from hw_checker.checks.deterministic.environment import path_tail
from hw_checker.checks.deterministic.http import evaluate_http
from hw_checker.domain import (
    ExecutionResult,
    FailureKind,
    NotEvaluatedOutcome,
    NotEvaluatedReason,
    PathFixKind,
    PathNotRewrittenReason,
    PathRewrite,
    ProbeResult,
    StepStatus,
)
from tests.unit.checks.test_build_and_http import (
    BUILD_REQUIREMENT,
    GET_ALL,
    HTTP_REQUIREMENT,
    check_input,
)

OK, FAILED, SKIPPED = StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED
UNADAPTED = PathRewrite(
    file_path="Repository/Store.cs",
    line=3,
    kind=PathFixKind.ABSOLUTE,
    original="D:\\Users\\someone\\Shop\\Data\\users.txt",
    not_rewritten_reason=PathNotRewrittenReason.SEVERAL_MATCHES,
)
DYNAMIC = UNADAPTED.model_copy(
    update={"original": "C:\\Data\\", "not_rewritten_reason": PathNotRewrittenReason.DYNAMIC}
)
NOT_PREPARED = ExecutionResult(
    prepare_status=FAILED,
    build_status=SKIPPED,
    run_status=SKIPPED,
    failure_kind=FailureKind.PROJECT_NOT_FOUND,
    logs_excerpt="several Web projects found: A/A.csproj, B/B.csproj; none could be chosen\n",
)
BUILD_TIMEOUT = ExecutionResult(
    build_status=FAILED,
    run_status=SKIPPED,
    failure_kind=FailureKind.TIMEOUT,
    logs_excerpt="\nrestore took longer than 300 s\n",
)
RUN_WITH_PATH = ExecutionResult(
    build_status=OK,
    run_status=FAILED,
    failure_kind=FailureKind.STARTUP_FAILED,
    path_rewrites=(UNADAPTED, DYNAMIC, UNADAPTED),
)
RUN_TIMEOUT = ExecutionResult(
    build_status=OK, run_status=FAILED, failure_kind=FailureKind.TIMEOUT, logs_excerpt=""
)
FAILED_PROBE = ProbeResult(probe=GET_ALL, actual_status=500, passed=False)
PROBE_WITH_PATH = ExecutionResult(
    build_status=OK, run_status=OK, probe_results=(FAILED_PROBE,), path_rewrites=(UNADAPTED,)
)
OTHER_PROBE = ProbeResult(
    probe=GET_ALL.model_copy(update={"requirement_id": "hw01-other"}),
    actual_status=200,
    passed=True,
)
NOT_PROBED = ExecutionResult(build_status=OK, run_status=OK, probe_results=(OTHER_PROBE,))

HTTP_CASES = {
    "prepare": (
        NOT_PREPARED,
        "the prepare step failed: several Web projects found: A/A.csproj, B/B.csproj; none "
        "could be chosen",
    ),
    "build": (
        BUILD_TIMEOUT,
        "the build step failed on the checking machine (timeout): restore took longer than 300 s",
    ),
    "run with a path": (
        RUN_WITH_PATH,
        "the run step failed (startup_failed), and the code has a path the run could not adapt "
        "to the container: `…\\Data\\users.txt` (several files of the repo match it), "
        "`C:\\Data` (it is built at run time) and 1 more",
    ),
    "not probed": (
        NOT_PROBED,
        "no request of this requirement was sent, although the app answered: the run's "
        "requests (1) were all for other requirements (was it run with this assignment's "
        "probes?)",
    ),
    "probe with a path": (
        PROBE_WITH_PATH,
        "a request failed, and the code has a path the run could not adapt to the container: "
        "`…\\Data\\users.txt` (several files of the repo match it)",
    ),
}


@pytest.mark.parametrize("case", HTTP_CASES)
def test_every_environment_issue_of_the_http_evaluator_says_what_happened(case: str) -> None:
    execution, detail = HTTP_CASES[case]

    outcome = evaluate_http(check_input(HTTP_REQUIREMENT, execution))

    assert outcome == NotEvaluatedOutcome(
        reason=NotEvaluatedReason.ENVIRONMENT_ISSUE, detail=detail
    )


@pytest.mark.parametrize(
    ("execution", "start"),
    [
        (NOT_PREPARED, "the prepare step failed: several Web projects found"),
        (BUILD_TIMEOUT, "the build step failed on the checking machine (timeout)"),
        (RUN_WITH_PATH, "the run step failed (startup_failed), and the code has a path"),
        (RUN_TIMEOUT, "the run step failed on the checking machine (timeout): no message"),
    ],
)
def test_every_environment_issue_of_the_build_evaluator_says_what_happened(
    execution: ExecutionResult, start: str
) -> None:
    outcome = evaluate_build(check_input(BUILD_REQUIREMENT, execution))

    assert isinstance(outcome, NotEvaluatedOutcome)
    assert outcome.reason is NotEvaluatedReason.ENVIRONMENT_ISSUE
    assert outcome.detail.startswith(start)


@pytest.mark.parametrize(
    ("path", "tail"),
    [
        ("D:\\Users\\someone\\Shop\\users.txt", "…\\Shop\\users.txt"),
        ("C:/x/y/z.json", "…/y/z.json"),
        ("C:\\users.txt", "C:\\users.txt"),
    ],
)
def test_a_path_is_shown_by_its_tail_only(path: str, tail: str) -> None:
    assert path_tail(path) == tail
    assert "someone" not in path_tail(path)


def test_a_long_detail_is_cut_to_what_an_entry_holds() -> None:
    long_log = "x" * 5000
    execution = NOT_PREPARED.model_copy(update={"logs_excerpt": long_log})

    outcome = evaluate_http(check_input(HTTP_REQUIREMENT, execution))

    assert isinstance(outcome, NotEvaluatedOutcome)
    assert len(outcome.detail) < 300
