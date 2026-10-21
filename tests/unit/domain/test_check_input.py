from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.domain import Assignment, CheckInput, ExecutionResult, StepStatus
from tests.unit.domain import builders


def assignment() -> Assignment:
    return Assignment(id="hw03", order=3, title="Products", requirements=(builders.requirement(),))


def test_check_input_builds_without_execution() -> None:
    check_input = CheckInput(
        repo_dir=Path("repo"), assignment=assignment(), requirement=builders.requirement()
    )

    assert check_input.execution is None


def test_check_input_accepts_an_execution_result() -> None:
    execution = ExecutionResult(build_status=StepStatus.SUCCEEDED, run_status=StepStatus.SUCCEEDED)

    check_input = CheckInput(
        repo_dir=Path("repo"),
        assignment=assignment(),
        requirement=builders.requirement(),
        execution=execution,
    )

    assert check_input.execution == execution


def test_check_input_rejects_a_requirement_of_another_assignment() -> None:
    with pytest.raises(ValidationError, match="not part of assignment 'hw03'"):
        CheckInput(
            repo_dir=Path("repo"),
            assignment=assignment(),
            requirement=builders.requirement(id="hw09-r1"),
        )


def test_check_input_is_frozen() -> None:
    check_input = CheckInput(
        repo_dir=Path("repo"), assignment=assignment(), requirement=builders.requirement()
    )

    with pytest.raises(ValidationError):
        check_input.repo_dir = Path("other")  # type: ignore[misc]
