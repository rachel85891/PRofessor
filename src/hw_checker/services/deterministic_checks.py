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
                not_evaluated.append(NotEvaluated(requirement_id=requirement.id, reason=outcome))
            else:
                findings.extend(outcome)
        return AssignmentResult(
            assignment_id=assignment.id,
            findings=tuple(findings),
            not_evaluated=tuple(not_evaluated),
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
