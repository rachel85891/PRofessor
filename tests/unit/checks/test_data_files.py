"""The note for a data file the repo lacks, which the run created empty (stage 22)."""

from pathlib import Path

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.deterministic.http import evaluate_http
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    CheckInput,
    ExecutionResult,
    FailureKind,
    Feedback,
    FindingSource,
    NotEvaluatedReason,
    PathFixKind,
    PathRewrite,
    ProbeResult,
    Requirement,
    RequirementKind,
    Severity,
    StepStatus,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.unit.checks.test_build_and_http import (
    GET_ALL,
    HTTP_REQUIREMENT,
    UNRESOLVED,
    check_input,
    with_paths,
)

OK = StepStatus.SUCCEEDED
DATA_FILE = Requirement(
    id="hw01-data-file-in-repo",
    title="The data file the code reads is in the repo",
    severity=Severity.MINOR,
    kind=RequirementKind.CUSTOM,
    feedback=Feedback.NOTES_ONLY,
    check="data_files_in_repo",
)
ASSIGNMENT = Assignment(
    id="hw01", order=1, title="Users", requirements=(HTTP_REQUIREMENT, DATA_FILE)
)
CHECKER = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))


def created(
    path: str, line: int = 4, literal: str = "D:\\Shop\\Repository\\users.txt"
) -> PathRewrite:
    return PathRewrite(
        file_path="Repository/Store.cs",
        line=line,
        kind=PathFixKind.ABSOLUTE,
        original=literal,
        rewritten=f"../{path}",
        created_path=path,
    )


def run(*rewrites: PathRewrite, probes: tuple[ProbeResult, ...] = ()) -> ExecutionResult:
    return ExecutionResult(
        build_status=OK, run_status=OK, path_rewrites=rewrites, probe_results=probes
    )


def data_input(execution: ExecutionResult | None) -> CheckInput:
    return CheckInput(
        repo_dir=Path("repo"), assignment=ASSIGNMENT, requirement=DATA_FILE, execution=execution
    )


def test_each_created_data_file_gets_one_minor_note() -> None:
    execution = run(
        created("Repository/users.txt"),
        created("Repository/users.txt", line=9, literal="E:\\x\\Repository\\users.txt"),
        created("Api/orders.txt", literal="D:\\orders.txt"),
    )

    outcome = CHECKER.check(data_input(execution))

    assert isinstance(outcome, tuple)
    assert [finding.message_en for finding in outcome] == [
        "`Repository/users.txt` isn't in the repo, so the app fails until it is created. Add "
        "the data file to the repo, or create it when it's missing.",
        "`Api/orders.txt` isn't in the repo, so the app fails until it is created. Add the "
        "data file to the repo, or create it when it's missing.",
    ]
    assert all(finding.message_he.startswith("`") for finding in outcome)
    assert {finding.severity for finding in outcome} == {Severity.MINOR}
    assert {finding.source for finding in outcome} == {FindingSource.DETERMINISTIC}
    assert len({finding.id for finding in outcome}) == 2
    assert "D:\\" not in " ".join(finding.message_en for finding in outcome)


def test_no_created_file_passes() -> None:
    assert CHECKER.check(data_input(run())) == ()
    assert CHECKER.check(data_input(run(UNRESOLVED))) == ()


def test_without_a_run_or_its_paths_it_is_not_evaluated() -> None:
    not_prepared = ExecutionResult(
        prepare_status=StepStatus.FAILED,
        build_status=StepStatus.SKIPPED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.PROJECT_NOT_FOUND,
    )

    assert CHECKER.check(data_input(None)) is NotEvaluatedReason.NO_EXECUTION
    assert CHECKER.check(data_input(not_prepared)) is NotEvaluatedReason.NO_EXECUTION


def test_with_the_file_created_the_http_probes_are_evaluated() -> None:
    passed = ProbeResult(probe=GET_ALL, actual_status=200, passed=True)
    failed = ProbeResult(probe=GET_ALL, actual_status=500, passed=False)

    assert (
        evaluate_http(check_input(HTTP_REQUIREMENT, run(created("x/users.txt"), probes=(passed,))))
        == ()
    )
    outcome = evaluate_http(
        check_input(HTTP_REQUIREMENT, run(created("x/users.txt"), probes=(failed,)))
    )
    assert isinstance(outcome, tuple) and len(outcome) == 1
    # A path that could not be adapted still makes a failing probe not evaluated.
    unresolved = with_paths(run(probes=(failed,)), UNRESOLVED)
    assert evaluate_http(check_input(HTTP_REQUIREMENT, unresolved)) is (
        NotEvaluatedReason.ENVIRONMENT_ISSUE
    )


def test_the_service_reports_the_note_once_per_path(tmp_path: Path) -> None:
    execution = run(created("Repository/users.txt"), created("Repository/users.txt", line=9))

    result = DeterministicCheckService(CHECKER).evaluate(ASSIGNMENT, tmp_path, execution)

    notes = [f for f in result.findings if f.requirement_id == DATA_FILE.id]
    assert len(notes) == 1
    assert all(entry.requirement_id != DATA_FILE.id for entry in result.not_evaluated)
