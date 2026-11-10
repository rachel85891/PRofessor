"""Synthetic assignments with `applies_when` / `skip_when`, and a test-only check.

`marker_file` holds when the working copy has a file named `name`; with `<name>.unknown`
instead, it cannot be evaluated. Nothing here comes from a real repo.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import Localized
from hw_checker.checks.registry import CheckRegistry, CheckResult, check
from hw_checker.domain import (
    Assignment,
    CheckCondition,
    CheckInput,
    CheckPassed,
    ConditionOutcome,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
)
from tests.fakes.checks import FakeRequirementChecker, Outcome

MARKER = "next-path.marker"
UNKNOWN_SUFFIX = ".unknown"


class MarkerParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str


@check("marker_file", MarkerParams, "Test only: the working copy has the file `name`.")
def marker_file(check_input: CheckInput, params: MarkerParams) -> CheckResult:
    if (check_input.repo_dir / f"{params.name}{UNKNOWN_SUFFIX}").exists():
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    if (check_input.repo_dir / params.name).exists():
        return CheckPassed(message=f"{params.name} is there")
    message = Localized(he=f"אין {params.name}", en=f"no {params.name}")
    return (deterministic_finding(check_input, message, [params.name]),)


REGISTRY = CheckRegistry([marker_file])


class ConditionChecker(FakeRequirementChecker):
    """The fake's outcomes for requirements; the real registry (with `marker_file`) for
    conditions, so each repo's condition depends on its own working copy."""

    def __init__(self, outcomes: dict[str, Outcome] | None = None) -> None:
        super().__init__(outcomes)
        self._real = DeterministicRequirementChecker(REGISTRY)
        self.conditions: list[CheckInput] = []

    def condition(self, check_input: CheckInput) -> ConditionOutcome:
        self.conditions.append(check_input)
        return self._real.condition(check_input)


def structure(requirement_id: str) -> Requirement:
    return Requirement(
        id=requirement_id,
        title=requirement_id,
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check="marker_file",
        params={"name": "never-there.txt"},
    )


def assignment(
    assignment_id: str,
    order: int,
    *,
    applies_when: str | None = None,
    skip_when: str | None = None,
    requirements: tuple[Requirement, ...] | None = None,
) -> Assignment:
    """An assignment whose condition (if any) is `marker_file` on the given marker."""
    return Assignment(
        id=assignment_id,
        order=order,
        title=f"Assignment {assignment_id}",
        requirements=requirements
        if requirements is not None
        else (structure(f"{assignment_id}-r1"), structure(f"{assignment_id}-r2")),
        applies_when=None
        if applies_when is None
        else CheckCondition(check="marker_file", params={"name": applies_when}),
        skip_when=None
        if skip_when is None
        else CheckCondition(check="marker_file", params={"name": skip_when}),
    )


def repo_with(root: Path, *names: str) -> Path:
    """A synthetic working copy holding the files `names`."""
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_text("synthetic\n", encoding="utf-8")
    return root
