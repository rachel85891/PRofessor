"""The real checker and service over the sample Web API fixture and a hand-built execution."""

from pathlib import Path

import pytest

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    CheckInput,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    NotEvaluated,
    NotEvaluatedReason,
    ProbeResult,
    Requirement,
    RequirementKind,
    Severity,
    StepStatus,
)
from hw_checker.domain.errors import InvalidAssignmentError
from hw_checker.ports import RequirementChecker
from hw_checker.services.deterministic_checks import DeterministicCheckService

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_webapi"


def http_probe(requirement_id: str, method: HttpMethod, path: str, status: int) -> HttpProbe:
    return HttpProbe(
        requirement_id=requirement_id,
        assignment_id="hw01",
        method=method,
        path=path,
        body='{"name":"Desk","price":250}' if method is HttpMethod.POST else None,
        expected_status=status,
    )


LIST = http_probe("hw01-r5", HttpMethod.GET, "/api/products", 200)
MISSING = http_probe("hw01-r5", HttpMethod.GET, "/api/products/999", 404)
CREATE = http_probe("hw01-r6", HttpMethod.POST, "/api/products", 201)


def structure(requirement_id: str, check: str, **params: object) -> Requirement:
    return Requirement(
        id=requirement_id,
        title=requirement_id,
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check=check,
        params=params,  # type: ignore[arg-type]
    )


ASSIGNMENT = Assignment(
    id="hw01",
    order=1,
    title="Products controller",
    requirements=(
        structure("hw01-r1", "file_exists", glob="**/Controllers/UsersController.cs"),
        structure("hw01-r2", "folder_exists", path="Models"),
        structure("hw01-r3", "file_exists", glob="**/Services/*.cs"),
        Requirement(
            id="hw01-r4", title="Builds", severity=Severity.CRITICAL, kind=RequirementKind.BUILD
        ),
        Requirement(
            id="hw01-r5",
            title="Read products",
            severity=Severity.MAJOR,
            kind=RequirementKind.HTTP,
            http_probes=(LIST, MISSING),
        ),
        Requirement(
            id="hw01-r6",
            title="Create product",
            severity=Severity.CRITICAL,
            kind=RequirementKind.HTTP,
            http_probes=(CREATE,),
        ),
        Requirement(
            id="hw01-r7", title="Null check", severity=Severity.MINOR, kind=RequirementKind.CODE
        ),
    ),
)

EXECUTION = ExecutionResult(
    build_status=StepStatus.SUCCEEDED,
    run_status=StepStatus.SUCCEEDED,
    probe_results=(
        ProbeResult(probe=LIST, actual_status=200, passed=True),
        ProbeResult(probe=MISSING, actual_status=404, passed=True),
        ProbeResult(probe=CREATE, actual_status=500, passed=False),
    ),
)


def checker() -> DeterministicRequirementChecker:
    return DeterministicRequirementChecker(CheckRegistry(builtin_checks()))


def test_checker_satisfies_the_port() -> None:
    port: RequirementChecker = checker()

    assert isinstance(port, DeterministicRequirementChecker)


def test_builtin_checks_are_registered() -> None:
    assert CheckRegistry(builtin_checks()).check_names() == {"file_exists", "folder_exists"}


def test_realistic_assignment_over_the_sample_api() -> None:
    result = DeterministicCheckService(checker()).evaluate(ASSIGNMENT, FIXTURE, EXECUTION)

    by_requirement = {finding.requirement_id: finding for finding in result.findings}
    assert set(by_requirement) == {"hw01-r3", "hw01-r6"}
    assert by_requirement["hw01-r3"].message_en == (
        "No file matching **/Services/*.cs was found in the repository."
    )
    assert by_requirement["hw01-r6"].message_en.endswith(
        "POST /api/products: expected 201, got 500"
    )
    assert by_requirement["hw01-r6"].severity is Severity.CRITICAL
    assert result.not_evaluated == ()


def test_same_round_twice_gives_the_same_finding_ids() -> None:
    service = DeterministicCheckService(checker())

    first = service.evaluate(ASSIGNMENT, FIXTURE, EXECUTION)
    second = service.evaluate(ASSIGNMENT, FIXTURE, EXECUTION)

    assert [f.id for f in first.findings] == [f.id for f in second.findings]


def test_without_execution_only_structure_is_judged() -> None:
    result = DeterministicCheckService(checker()).evaluate(ASSIGNMENT, FIXTURE, None)

    assert [f.requirement_id for f in result.findings] == ["hw01-r3"]
    assert result.not_evaluated == tuple(
        NotEvaluated(requirement_id=requirement_id, reason=NotEvaluatedReason.NO_EXECUTION)
        for requirement_id in ("hw01-r4", "hw01-r5", "hw01-r6")
    )


def test_code_requirement_is_rejected_by_the_checker() -> None:
    code = ASSIGNMENT.requirements[-1]

    with pytest.raises(InvalidAssignmentError, match="code requirement"):
        checker().check(CheckInput(repo_dir=FIXTURE, assignment=ASSIGNMENT, requirement=code))


def test_unknown_check_and_invalid_params_are_invalid_assignment_data() -> None:
    unknown = structure("hw01-r8", "no_such_check")
    invalid = structure("hw01-r9", "file_exists", glob="../outside/*.cs")
    assignment = Assignment(id="hw01", order=1, title="Bad", requirements=(unknown, invalid))

    for requirement in (unknown, invalid):
        with pytest.raises(InvalidAssignmentError, match=requirement.id):
            checker().check(
                CheckInput(repo_dir=FIXTURE, assignment=assignment, requirement=requirement)
            )
