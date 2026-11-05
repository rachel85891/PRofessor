"""Deterministic check service: evaluates the non-LLM requirements of one assignment."""

import logging
from pathlib import Path

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    CheckInput,
    ExecutionResult,
    Finding,
    NotEvaluated,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
)
from hw_checker.domain.errors import InvalidAssignmentError
from hw_checker.ports import RequirementChecker

logger = logging.getLogger(__name__)

DETERMINISTIC_KINDS = frozenset(
    {RequirementKind.STRUCTURE, RequirementKind.CUSTOM, RequirementKind.BUILD, RequirementKind.HTTP}
)


class DeterministicCheckService:
    """Turns an assignment's structure, custom, build and http requirements into findings.

    `code` requirements are left to the LLM reviewer. A requirement that cannot be checked
    is listed as not evaluated, with its reason, instead of becoming a finding.
    """

    def __init__(self, checker: RequirementChecker) -> None:
        self._checker = checker

    def evaluate(
        self, assignment: Assignment, repo_dir: Path, execution: ExecutionResult | None
    ) -> AssignmentResult:
        """Evaluate every deterministic requirement of `assignment` against the working copy.

        Raises:
            InvalidAssignmentError: a requirement names an unknown check or invalid params.
        """
        findings: list[Finding] = []
        not_evaluated: list[NotEvaluated] = []
        paths = ", ".join(execution.windows_paths) if execution else ""
        for requirement in assignment.requirements:
            if requirement.kind not in DETERMINISTIC_KINDS:
                continue
            check_input = CheckInput(
                repo_dir=repo_dir,
                assignment=assignment,
                requirement=requirement,
                execution=execution,
            )
            outcome = self._check(requirement, check_input)
            if isinstance(outcome, NotEvaluatedReason):
                detail = paths if outcome is NotEvaluatedReason.WINDOWS_PATH else ""
                not_evaluated.append(
                    NotEvaluated(requirement_id=requirement.id, reason=outcome, detail=detail)
                )
            else:
                findings.extend(outcome)
        return _windows_path_dependents(
            assignment,
            AssignmentResult(
                assignment_id=assignment.id,
                findings=tuple(findings),
                not_evaluated=tuple(not_evaluated),
            ),
            paths,
        )

    def _check(
        self, requirement: Requirement, check_input: CheckInput
    ) -> tuple[Finding, ...] | NotEvaluatedReason:
        try:
            return self._checker.check(check_input)
        except InvalidAssignmentError:
            raise  # bad requirement data stops the round; it is not the student's repo
        except Exception as error:
            # Deliberate boundary: checks read untrusted student repos, so one check failing
            # in an unexpected way must not hide the results of the other requirements.
            logger.warning(
                "Check of requirement %s failed (%s); marked not evaluated",
                requirement.id,
                type(error).__name__,
                extra={"requirement_id": requirement.id},
            )
            return NotEvaluatedReason.ENVIRONMENT_ISSUE


def _windows_path_dependents(
    assignment: Assignment, result: AssignmentResult, paths: str
) -> AssignmentResult:
    """A requirement that failed or was not evaluated while it depends (`depends_on`, also
    through others) on a `windows_path` requirement is `windows_path` too: its failure is
    likely the same container problem."""
    reached = {
        entry.requirement_id
        for entry in result.not_evaluated
        if entry.reason is NotEvaluatedReason.WINDOWS_PATH
    }
    depends = {
        requirement.id: set(requirement.depends_on) for requirement in assignment.requirements
    }
    while True:
        more = {item for item, needs in depends.items() if needs & reached} - reached
        if not more:
            break
        reached |= more
    failing = [finding.requirement_id for finding in result.findings]
    failing += [entry.requirement_id for entry in result.not_evaluated]
    moved = [
        requirement_id
        for requirement_id in dict.fromkeys(failing)
        if requirement_id in reached and requirement_id not in _windows(result)
    ]
    if not moved:
        return result
    kept = [entry for entry in result.not_evaluated if entry.requirement_id not in moved]
    added = [
        NotEvaluated(
            requirement_id=requirement_id, reason=NotEvaluatedReason.WINDOWS_PATH, detail=paths
        )
        for requirement_id in moved
    ]
    return result.model_copy(
        update={
            "findings": tuple(f for f in result.findings if f.requirement_id not in moved),
            "not_evaluated": tuple(kept + added),
        }
    )


def _windows(result: AssignmentResult) -> set[str]:
    return {
        entry.requirement_id
        for entry in result.not_evaluated
        if entry.reason is NotEvaluatedReason.WINDOWS_PATH
    }
