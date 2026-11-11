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
    TrackedFiles,
)
from hw_checker.domain.errors import InvalidAssignmentError
from hw_checker.services.deterministic_checks import DeterministicCheckService
from tests.fakes.checks import FakeRequirementChecker
from tests.fakes.git import FakeGitClient
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
        NotEvaluated(requirement_id="hw01-r4", reason=NotEvaluatedReason.AWAITING_REVIEWER),
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
        NotEvaluated(requirement_id="hw01-r4", reason=NotEvaluatedReason.AWAITING_REVIEWER),
    )
    assert [f.requirement_id for f in result.findings] == ["hw01-r5"]
    (record,) = caplog.records
    assert "hw01-r1" in record.getMessage() and "PermissionError" in record.getMessage()
    assert "access denied" not in record.getMessage()


def test_invalid_requirement_data_stops_the_evaluation() -> None:
    checker = FakeRequirementChecker({"hw01-r1": InvalidAssignmentError("unknown check")})

    with pytest.raises(InvalidAssignmentError, match="unknown check"):
        DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)


def test_a_code_requirement_waits_for_the_reviewer_and_is_never_a_finding() -> None:
    only_code = Assignment(
        id="hw01",
        order=1,
        title="Code",
        requirements=(requirement("hw01-r4", RequirementKind.CODE),),
    )

    checker = FakeRequirementChecker()
    result = DeterministicCheckService(checker).evaluate(only_code, REPO, None)

    assert result.findings == ()
    assert result.not_evaluated == (
        NotEvaluated(requirement_id="hw01-r4", reason=NotEvaluatedReason.AWAITING_REVIEWER),
    )
    assert checker.checked == []


def test_tracked_files_are_read_once_and_given_to_every_check(tmp_path: Path) -> None:
    (tmp_path / "Program.cs").write_text("class P {}\n", encoding="utf-8")
    git = FakeGitClient(ignored=frozenset({"Program.cs"}))
    checker = FakeRequirementChecker()

    DeterministicCheckService(checker, git).evaluate(ASSIGNMENT, tmp_path, EXECUTION)

    expected = TrackedFiles(paths=("Program.cs",), ignored=("Program.cs",))
    assert all(c.tracked == expected for c in checker.checked)


def test_unreadable_git_state_leaves_tracked_files_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    checker = FakeRequirementChecker()

    with caplog.at_level(logging.WARNING, logger="hw_checker.services.deterministic_checks"):
        DeterministicCheckService(checker, FakeGitClient()).evaluate(
            ASSIGNMENT, tmp_path / "missing", EXECUTION
        )

    assert all(c.tracked is None for c in checker.checked)
    assert "RepoUnavailableError" in caplog.text


def test_without_a_git_client_tracked_files_are_not_read() -> None:
    checker = FakeRequirementChecker()

    DeterministicCheckService(checker).evaluate(ASSIGNMENT, REPO, EXECUTION)

    assert all(c.tracked is None for c in checker.checked)


def test_a_registered_check_whose_dependency_failed_is_not_run() -> None:
    def needing(base: Requirement, *depends_on: str) -> Requirement:
        return base.model_copy(update={"depends_on": depends_on})

    layers = requirement("hw02-layers", RequirementKind.STRUCTURE)
    references = needing(requirement("hw02-references", RequirementKind.STRUCTURE), "hw02-layers")
    deeper = needing(requirement("hw02-deeper", RequirementKind.CUSTOM), "hw02-references")
    probe = needing(requirement("hw01-r3", RequirementKind.HTTP), "hw02-layers")
    passing = requirement("hw02-passing", RequirementKind.STRUCTURE)
    after_pass = needing(requirement("hw02-after-pass", RequirementKind.STRUCTURE), "hw02-passing")
    assignment = Assignment(
        id="hw01",
        order=1,
        title="Layers",
        requirements=(layers, references, deeper, probe, passing, after_pass),
    )
    checker = FakeRequirementChecker({"hw02-layers": (finding("hw02-layers"),)})

    result = DeterministicCheckService(checker).evaluate(assignment, REPO, EXECUTION)

    assert [f.requirement_id for f in result.findings] == ["hw02-layers"]
    assert result.not_evaluated == (
        NotEvaluated(
            requirement_id="hw02-references",
            reason=NotEvaluatedReason.DEPENDENCY_FAILED,
            detail="`hw02-layers`",
        ),
        NotEvaluated(
            requirement_id="hw02-deeper",
            reason=NotEvaluatedReason.DEPENDENCY_FAILED,
            detail="`hw02-references`",
        ),
    )
    assert [item.requirement.id for item in checker.checked] == [
        "hw02-layers",
        "hw01-r3",
        "hw02-passing",
        "hw02-after-pass",
    ]
