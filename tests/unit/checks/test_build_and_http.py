from pathlib import Path

import pytest

from hw_checker.checks.deterministic.build import (
    evaluate_build,
    first_compiler_error,
    first_startup_crash,
)
from hw_checker.checks.deterministic.http import evaluate_http
from hw_checker.domain import (
    Assignment,
    CheckInput,
    ExecutionResult,
    FailureKind,
    Finding,
    HttpMethod,
    HttpProbe,
    NotEvaluatedReason,
    PathFixKind,
    PathNotRewrittenReason,
    PathRewrite,
    ProbeResult,
    Requirement,
    RequirementKind,
    RouteResolution,
    Severity,
    StepStatus,
)

OK, FAILED, SKIPPED = StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED
BUILD_LOG = (
    "/work/src/Controllers/ProductsController.cs(3,20): error CS0103: The name 'produts' does "
    "not exist in the current context [/work/src/SampleWebApi.csproj]"
)


def probe(
    path: str, expected: int, allowed: tuple[int, ...] = (), method: HttpMethod = HttpMethod.GET
) -> HttpProbe:
    return HttpProbe(
        requirement_id="hw01-r3",
        assignment_id="hw01",
        method=method,
        path=path,
        expected_status=expected,
        allowed_statuses=allowed,
    )


GET_ALL = probe("/api/products", 200)
GET_MISSING = probe("/api/products/999", 404, (404, 204))
CREATE = probe("/api/products", 201, (201, 200), HttpMethod.POST)
BUILD_REQUIREMENT = Requirement(
    id="hw01-r2", title="Builds", severity=Severity.CRITICAL, kind=RequirementKind.BUILD
)
HTTP_REQUIREMENT = Requirement(
    id="hw01-r3",
    title="Products API answers",
    severity=Severity.MAJOR,
    kind=RequirementKind.HTTP,
    http_probes=(GET_ALL, GET_MISSING, CREATE),
)
ASSIGNMENT = Assignment(
    id="hw01", order=1, title="Products", requirements=(BUILD_REQUIREMENT, HTTP_REQUIREMENT)
)


def check_input(
    requirement: Requirement, execution: ExecutionResult | None, repo: Path = Path("repo")
) -> CheckInput:
    return CheckInput(
        repo_dir=repo, assignment=ASSIGNMENT, requirement=requirement, execution=execution
    )


def run_ok(*results: ProbeResult) -> ExecutionResult:
    return ExecutionResult(build_status=OK, run_status=OK, probe_results=results)


def result(probe_: HttpProbe, status: int | None) -> ProbeResult:
    return ProbeResult(probe=probe_, actual_status=status, passed=status in probe_.allowed_statuses)


# ---------------------------------------------------------------- build


def failure(kind: FailureKind) -> ExecutionResult:
    if kind is FailureKind.PROJECT_NOT_FOUND:
        return ExecutionResult(
            prepare_status=FAILED, build_status=SKIPPED, run_status=SKIPPED, failure_kind=kind
        )
    if kind in (FailureKind.STARTUP_FAILED, FailureKind.DB_UNREACHABLE):
        return ExecutionResult(build_status=OK, run_status=FAILED, failure_kind=kind)
    return ExecutionResult(
        build_status=FAILED, run_status=SKIPPED, failure_kind=kind, logs_excerpt=BUILD_LOG
    )


EXPECTED_BUILD = {
    FailureKind.BUILD_FAILED: "finding",
    FailureKind.STARTUP_FAILED: "finding",
    FailureKind.DB_UNREACHABLE: "pass",
    FailureKind.HARDCODED_CONNECTION_UNRESOLVED: NotEvaluatedReason.ENVIRONMENT_ISSUE,
    FailureKind.TIMEOUT: NotEvaluatedReason.ENVIRONMENT_ISSUE,
    FailureKind.ENVIRONMENT_ERROR: NotEvaluatedReason.ENVIRONMENT_ISSUE,
    FailureKind.PROJECT_NOT_FOUND: NotEvaluatedReason.ENVIRONMENT_ISSUE,
}


def test_every_failure_kind_has_an_expected_build_outcome() -> None:
    assert set(EXPECTED_BUILD) == set(FailureKind)


@pytest.mark.parametrize("kind", list(FailureKind))
def test_build_outcome_per_failure_kind(kind: FailureKind) -> None:
    outcome = evaluate_build(check_input(BUILD_REQUIREMENT, failure(kind)))

    expected = EXPECTED_BUILD[kind]
    if expected == "finding":
        assert isinstance(outcome, tuple) and len(outcome) == 1
    elif expected == "pass":
        assert outcome == ()
    else:
        assert outcome is expected


def test_successful_build_passes() -> None:
    assert evaluate_build(check_input(BUILD_REQUIREMENT, run_ok())) == ()


def test_build_without_execution_is_not_evaluated() -> None:
    assert evaluate_build(check_input(BUILD_REQUIREMENT, None)) is NotEvaluatedReason.NO_EXECUTION


def test_build_finding_names_the_first_compiler_error_with_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "Controllers").mkdir(parents=True)
    (repo / "Controllers" / "ProductsController.cs").write_text(
        "public class C {\n  int x;\n  public object Get() => Ok(produts);\n}\n", encoding="utf-8"
    )

    (finding,) = _findings(
        evaluate_build(check_input(BUILD_REQUIREMENT, failure(FailureKind.BUILD_FAILED), repo))
    )

    assert finding.message_en == (
        "The project does not build. First compiler error: CS0103: The name 'produts' does not "
        "exist in the current context"
    )
    assert finding.severity is Severity.CRITICAL
    assert finding.evidence is not None
    assert (finding.evidence.file_path, finding.evidence.line_start) == (
        "Controllers/ProductsController.cs",
        3,
    )
    assert finding.evidence.quote == "public object Get() => Ok(produts);"


def test_build_finding_without_a_readable_file_has_no_evidence() -> None:
    (finding,) = _findings(
        evaluate_build(check_input(BUILD_REQUIREMENT, failure(FailureKind.BUILD_FAILED)))
    )

    assert finding.evidence is None


def test_build_failure_without_a_compiler_error_uses_the_generic_message() -> None:
    execution = ExecutionResult(
        build_status=FAILED,
        run_status=SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
        logs_excerpt="error MSB4025: The project file could not be loaded.",
    )

    (finding,) = _findings(evaluate_build(check_input(BUILD_REQUIREMENT, execution)))

    assert finding.message_en == "The project does not build. The build log has the details."


def test_compiler_error_outside_the_working_copy_is_ignored() -> None:
    assert first_compiler_error("/usr/share/dotnet/x.cs(1,1): error CS0001: boom") is None


STARTUP_LOG = (
    "info: Microsoft.Hosting.Lifetime[0]\n"
    "Unhandled exception. System.InvalidOperationException: Unable to resolve service for type "
    "'IProductService'.\n"
    "   at Microsoft.Extensions.DependencyInjection.ServiceProvider.GetService(Type t)\n"
    "   at Program.<Main>$(String[] args) in /work/src/Program.cs:line 2\n"
)


def startup_crash(log: str) -> ExecutionResult:
    return ExecutionResult(
        build_status=OK,
        run_status=FAILED,
        failure_kind=FailureKind.STARTUP_FAILED,
        logs_excerpt=log,
    )


def test_startup_crash_finding_names_the_exception_line_with_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Program.cs").write_text(
        "var app = WebApplication.Create();\napp.Services.GetRequiredService<IProductService>();\n",
        encoding="utf-8",
    )

    (finding,) = _findings(
        evaluate_build(check_input(BUILD_REQUIREMENT, startup_crash(STARTUP_LOG), repo))
    )

    assert finding.message_en == (
        "The project builds but crashes on startup. The exception: Unhandled exception. "
        "System.InvalidOperationException: Unable to resolve service for type 'IProductService'."
    )
    assert "System.InvalidOperationException" in finding.message_he
    assert finding.severity is Severity.CRITICAL
    assert finding.evidence is not None
    assert (finding.evidence.file_path, finding.evidence.line_start) == ("Program.cs", 2)
    assert finding.evidence.quote == "app.Services.GetRequiredService<IProductService>();"


def test_startup_crash_without_a_file_in_the_working_copy_has_no_evidence() -> None:
    (finding,) = _findings(
        evaluate_build(check_input(BUILD_REQUIREMENT, startup_crash(STARTUP_LOG)))
    )

    assert finding.evidence is None


def test_startup_crash_without_an_exception_line_uses_the_generic_message() -> None:
    execution = startup_crash("info: Microsoft.Hosting.Lifetime[0]\nApplication is shutting down")

    (finding,) = _findings(evaluate_build(check_input(BUILD_REQUIREMENT, execution)))

    assert finding.message_en == (
        "The project builds but crashes on startup. The run log has the details."
    )
    assert finding.evidence is None


@pytest.mark.parametrize("kind", [FailureKind.TIMEOUT, FailureKind.ENVIRONMENT_ERROR])
def test_run_timeout_or_environment_error_is_not_evaluated(kind: FailureKind) -> None:
    execution = ExecutionResult(
        build_status=OK, run_status=FAILED, failure_kind=kind, logs_excerpt=STARTUP_LOG
    )

    outcome = evaluate_build(check_input(BUILD_REQUIREMENT, execution))

    assert outcome is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_startup_finding_id_depends_on_the_exception_type() -> None:
    def finding_id(log: str) -> str:
        (finding,) = _findings(evaluate_build(check_input(BUILD_REQUIREMENT, startup_crash(log))))
        return finding.id

    other_type = STARTUP_LOG.replace("InvalidOperationException", "ArgumentNullException")

    assert finding_id(STARTUP_LOG) == finding_id(STARTUP_LOG)
    assert finding_id(STARTUP_LOG) != finding_id(other_type)
    assert finding_id(STARTUP_LOG) != finding_id("no exception here")


def test_startup_crash_parsing() -> None:
    crash = first_startup_crash(STARTUP_LOG)

    assert crash is not None
    assert crash.exception_type == "System.InvalidOperationException"
    assert (crash.path, crash.source_line) == ("Program.cs", 2)
    assert first_startup_crash("Unhandled exception.") == first_startup_crash(
        "x\nUnhandled exception.\n   at A.B() in /usr/lib/x.cs:line 3"
    )
    assert first_startup_crash("all good") is None


REWRITTEN = PathRewrite(
    file_path="Store.cs",
    line=4,
    kind=PathFixKind.ABSOLUTE,
    original="C:\\Projects\\Shop\\users.txt",
    rewritten="users.txt",
)
UNRESOLVED = PathRewrite(
    file_path="Store.cs",
    line=9,
    kind=PathFixKind.ABSOLUTE,
    original="C:\\Projects\\Shop\\orders.txt",
    not_rewritten_reason=PathNotRewrittenReason.NO_MATCH,
)


def with_paths(execution: ExecutionResult, *rewrites: PathRewrite) -> ExecutionResult:
    return execution.model_copy(update={"path_rewrites": rewrites})


def test_startup_crash_with_an_unresolved_path_is_not_evaluated() -> None:
    execution = with_paths(startup_crash(STARTUP_LOG), REWRITTEN, UNRESOLVED)

    outcome = evaluate_build(check_input(BUILD_REQUIREMENT, execution))

    assert outcome is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_startup_crash_with_every_path_rewritten_is_a_finding() -> None:
    execution = with_paths(startup_crash(STARTUP_LOG), REWRITTEN)

    assert len(_findings(evaluate_build(check_input(BUILD_REQUIREMENT, execution)))) == 1


def test_compile_error_is_a_finding_even_with_an_unresolved_path() -> None:
    execution = with_paths(failure(FailureKind.BUILD_FAILED), UNRESOLVED)

    assert len(_findings(evaluate_build(check_input(BUILD_REQUIREMENT, execution)))) == 1


def _findings(outcome: tuple[Finding, ...] | NotEvaluatedReason) -> tuple[Finding, ...]:
    assert isinstance(outcome, tuple)
    return outcome


# ---------------------------------------------------------------- http


def test_all_probes_passing_gives_no_finding() -> None:
    execution = run_ok(result(GET_ALL, 200), result(GET_MISSING, 404), result(CREATE, 201))

    assert evaluate_http(check_input(HTTP_REQUIREMENT, execution)) == ()


def test_allowed_alternative_status_passes() -> None:
    execution = run_ok(result(GET_ALL, 200), result(GET_MISSING, 204), result(CREATE, 200))

    assert evaluate_http(check_input(HTTP_REQUIREMENT, execution)) == ()


def test_one_failing_probe_gives_one_finding() -> None:
    execution = run_ok(result(GET_ALL, 200), result(GET_MISSING, 200), result(CREATE, 201))

    (finding,) = _findings(evaluate_http(check_input(HTTP_REQUIREMENT, execution)))

    assert finding.message_en == (
        "HTTP requests that did not return the expected status:\n"
        "GET /api/products/999: expected 404 or 204, got 200"
    )
    assert "GET /api/products/999: צפוי 404 או 204, התקבל 200" in finding.message_he
    assert finding.evidence is None


def test_two_failing_probes_give_a_single_finding_naming_both() -> None:
    execution = run_ok(result(GET_ALL, 500), result(GET_MISSING, 404), result(CREATE, None))

    (finding,) = _findings(evaluate_http(check_input(HTTP_REQUIREMENT, execution)))

    assert finding.message_en.splitlines()[1:] == [
        "GET /api/products: expected 200, got 500",
        "POST /api/products: expected 201 or 200, no answer",
    ]


@pytest.mark.parametrize(
    ("execution", "reason"),
    [
        (None, NotEvaluatedReason.NO_EXECUTION),
        (failure(FailureKind.BUILD_FAILED), NotEvaluatedReason.BUILD_FAILED),
        (failure(FailureKind.TIMEOUT), NotEvaluatedReason.ENVIRONMENT_ISSUE),
        (failure(FailureKind.ENVIRONMENT_ERROR), NotEvaluatedReason.ENVIRONMENT_ISSUE),
        (failure(FailureKind.STARTUP_FAILED), NotEvaluatedReason.RUN_NOT_HEALTHY),
        (failure(FailureKind.PROJECT_NOT_FOUND), NotEvaluatedReason.ENVIRONMENT_ISSUE),
        (run_ok(), NotEvaluatedReason.ENVIRONMENT_ISSUE),
    ],
)
def test_http_requirement_not_evaluated_without_a_healthy_run(
    execution: ExecutionResult | None, reason: NotEvaluatedReason
) -> None:
    assert evaluate_http(check_input(HTTP_REQUIREMENT, execution)) is reason


def test_failing_probe_with_an_unresolved_path_is_not_evaluated() -> None:
    execution = with_paths(
        run_ok(result(GET_ALL, 500), result(GET_MISSING, 404), result(CREATE, 201)),
        REWRITTEN,
        UNRESOLVED,
    )

    outcome = evaluate_http(check_input(HTTP_REQUIREMENT, execution))

    assert outcome is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_passing_probes_with_an_unresolved_path_still_pass() -> None:
    execution = with_paths(
        run_ok(result(GET_ALL, 200), result(GET_MISSING, 404), result(CREATE, 201)), UNRESOLVED
    )

    assert evaluate_http(check_input(HTTP_REQUIREMENT, execution)) == ()


def test_unhealthy_run_with_an_unresolved_path_is_an_environment_issue() -> None:
    execution = with_paths(failure(FailureKind.STARTUP_FAILED), UNRESOLVED)

    outcome = evaluate_http(check_input(HTTP_REQUIREMENT, execution))

    assert outcome is NotEvaluatedReason.ENVIRONMENT_ISSUE


def test_failing_probe_with_every_path_rewritten_is_a_finding() -> None:
    execution = with_paths(
        run_ok(result(GET_ALL, 500), result(GET_MISSING, 404), result(CREATE, 201)), REWRITTEN
    )

    assert len(_findings(evaluate_http(check_input(HTTP_REQUIREMENT, execution)))) == 1


def not_sent(probe_: HttpProbe) -> ProbeResult:
    return ProbeResult(probe=probe_, passed=False, route_resolution=RouteResolution.ROUTE_NOT_FOUND)


def test_probe_with_no_declared_route_makes_the_requirement_not_evaluated() -> None:
    execution = run_ok(result(GET_ALL, 500), result(GET_MISSING, 404), not_sent(CREATE))

    outcome = evaluate_http(check_input(HTTP_REQUIREMENT, execution))

    assert outcome is NotEvaluatedReason.ROUTE_NOT_FOUND


def test_resolved_probes_are_evaluated_as_before() -> None:
    def resolved(probe_: HttpProbe, status: int) -> ProbeResult:
        return ProbeResult(
            probe=probe_,
            actual_status=status,
            passed=status in probe_.allowed_statuses,
            sent_path=probe_.path,
            route_resolution=RouteResolution.RESOLVED,
        )

    passing = run_ok(resolved(GET_ALL, 200), resolved(GET_MISSING, 404), resolved(CREATE, 201))
    failing = run_ok(resolved(GET_ALL, 200), resolved(GET_MISSING, 404), resolved(CREATE, 500))

    assert evaluate_http(check_input(HTTP_REQUIREMENT, passing)) == ()
    assert len(_findings(evaluate_http(check_input(HTTP_REQUIREMENT, failing)))) == 1


def test_probes_of_other_requirements_are_ignored() -> None:
    other = HttpProbe(
        requirement_id="hw01-r9",
        assignment_id="hw01",
        method=HttpMethod.GET,
        path="/x",
        expected_status=200,
    )
    execution = run_ok(result(GET_ALL, 200), result(other, 500))

    assert evaluate_http(check_input(HTTP_REQUIREMENT, execution)) == ()


def test_http_finding_id_is_stable_and_depends_on_the_failure() -> None:
    first = run_ok(result(GET_ALL, 500))
    other = run_ok(result(GET_ALL, 404))

    a = _findings(evaluate_http(check_input(HTTP_REQUIREMENT, first)))[0].id
    b = _findings(evaluate_http(check_input(HTTP_REQUIREMENT, first)))[0].id
    c = _findings(evaluate_http(check_input(HTTP_REQUIREMENT, other)))[0].id

    assert a == b != c
