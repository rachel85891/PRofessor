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
    TrackedFiles,
)
from hw_checker.domain.errors import HwCheckerError, InvalidAssignmentError
from hw_checker.ports import GitClient, RequirementChecker

logger = logging.getLogger(__name__)

DETERMINISTIC_KINDS = frozenset(
    {RequirementKind.STRUCTURE, RequirementKind.CUSTOM, RequirementKind.BUILD, RequirementKind.HTTP}
)


class DeterministicCheckService:
    """Turns an assignment's structure, custom, build and http requirements into findings.

    `code` requirements are left to the LLM reviewer. A requirement that cannot be checked
    is listed as not evaluated, with its reason, instead of becoming a finding. With a
    `git` client, the working copy's tracked files are read once per evaluation for the
    checks that look at git's state; without one, those checks are not evaluated.
    """

    def __init__(self, checker: RequirementChecker, git: GitClient | None = None) -> None:
        self._checker = checker
        self._git = git

    def evaluate(
        self, assignment: Assignment, repo_dir: Path, execution: ExecutionResult | None
    ) -> AssignmentResult:
        """Evaluate every deterministic requirement of `assignment` against the working copy.

        Raises:
            InvalidAssignmentError: a requirement names an unknown check or invalid params.
        """
        findings: list[Finding] = []
        not_evaluated: list[NotEvaluated] = []
        tracked = self._tracked_files(repo_dir)
        for requirement in assignment.requirements:
            if requirement.kind not in DETERMINISTIC_KINDS:
                continue
            check_input = CheckInput(
                repo_dir=repo_dir,
                assignment=assignment,
                requirement=requirement,
                execution=execution,
                tracked=tracked,
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

    def _tracked_files(self, repo_dir: Path) -> TrackedFiles | None:
        if self._git is None:
            return None
        try:
            return self._git.tracked_files(repo_dir)
        except HwCheckerError as error:
            logger.warning(
                "Could not read the tracked files (%s); the git-state checks are not evaluated",
                type(error).__name__,
            )
            return None

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
