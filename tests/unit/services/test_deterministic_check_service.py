import logging
from pathlib import Path

import pytest

from hw_checker.domain import (
    Assignment,
    ExecutionResult,
    Finding,
    FindingSource,
    NotEvaluated,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
    StepStatus,
)
from hw_checker.domain.errors import InvalidAssignmentError
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.fakes.checks import FakeRequirementChecker
from tests.unit.domain import builders

REPO = Path("repo")
EXECUTION = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)


def requirement(requirement_id: str, kind: RequirementKind) -> Requirement:
    extra: dict[str, object] = {}
    if kind in (RequirementKind.STRUCTURE, RequirementKind.CUSTOM):
        extra = {"check": "file_exists", "params": {"glob": "*.cs"}}
    if kind is RequirementKind.HTTP:
        extra = {
            "http_probes": (builders.probe(requirement_id=requirement_id, assignment_id="hw01"),)
        }
    return Requirement.model_validate(
        {"id": requirement_id, "title": requirement_id, "severity": Severity.MAJOR, "kind": kind}
        | extra
    )


ASSIGNMENT = Assignment(
    id="hw01",
    order=1,
    title="Products",
    requirements=(
        requirement("hw01-r1", RequirementKind.STRUCTURE),
        requirement("hw01-r2", RequirementKind.BUILD),
        requirement("hw01-r3", RequirementKind.HTTP),
        requirement("hw01-r4", RequirementKind.CODE),
        requirement("hw01-r5", RequirementKind.CUSTOM),
    ),
)


def finding(requirement_id: str) -> Finding:
    return Finding(
        id=f"{requirement_id}-abc",
        requirement_id=requirement_id,
        assignment_id="hw01",
        severity=Severity.MAJOR,
        message_he="חסר קובץ",
        message_en="A file is missing.",
        source=FindingSource.DETERMINISTIC,
        confidence=1.0,
    )


def test_mixed_assignment_gives_findings_and_not_evaluated_entries() -> None:
    checker = FakeRequirementChecker(
        {
            "hw01-r1": (finding("hw01-r1"),),
            "hw01-r3": NotEvaluatedReason.BUILD_FAILED,
        }
    )

    result = DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)

    assert result.assignment_id == "hw01"
    assert [f.requirement_id for f in result.findings] == ["hw01-r1"]
    assert result.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r3", reason=NotEvaluatedReason.BUILD_FAILED),
    )


def test_code_requirements_are_left_out() -> None:
    checker = FakeRequirementChecker()

    DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)

    assert [c.requirement.id for c in checker.checked] == [
        "hw01-r1",
        "hw01-r2",
        "hw01-r3",
        "hw01-r5",
    ]
    assert all(c.execution == EXECUTION and c.repo_dir == REPO for c in checker.checked)


def test_a_check_raising_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    checker = FakeRequirementChecker(
        {"hw01-r1": PermissionError("access denied"), "hw01-r5": (finding("hw01-r5"),)}
    )

    with caplog.at_level(logging.WARNING, logger="hw_checker.services.deterministic_checks"):
        result = DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)

    assert result.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r1", reason=NotEvaluatedReason.ENVIRONMENT_ISSUE),
    )
    assert [f.requirement_id for f in result.findings] == ["hw01-r5"]
    (record,) = caplog.records
    assert "hw01-r1" in record.getMessage() and "PermissionError" in record.getMessage()
    assert "access denied" not in record.getMessage()


def test_invalid_requirement_data_stops_the_evaluation() -> None:
    checker = FakeRequirementChecker({"hw01-r1": InvalidAssignmentError("unknown check")})

    with pytest.raises(InvalidAssignmentError, match="unknown check"):
        DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)


def test_an_assignment_without_deterministic_requirements_gives_an_empty_result() -> None:
    only_code = Assignment(
        id="hw01",
        order=1,
        title="Code",
        requirements=(requirement("hw01-r4", RequirementKind.CODE),),
    )

    result = DeterministicCheckService(FakeRequirementChecker()).evaluate(only_code, REPO, None)

    assert (result.findings, result.not_evaluated) == ((), ())
