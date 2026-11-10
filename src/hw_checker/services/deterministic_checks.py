"""Deterministic check service: evaluates the non-LLM requirements of one assignment."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hw_checker.domain import (
    ArchitectureEvidence,
    Assignment,
    AssignmentResult,
    CheckInput,
    ConditionOutcome,
    ExecutionResult,
    Finding,
    NotEvaluated,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    TrackedFiles,
)
from hw_checker.domain.errors import HwCheckerError, InvalidAssignmentError
from hw_checker.domain.run import MAX_NOT_EVALUATED_DETAIL_CHARS
from hw_checker.ports import ArchitectureEvidenceReader, GitClient, RequirementChecker

logger = logging.getLogger(__name__)

DETERMINISTIC_KINDS = frozenset(
    {RequirementKind.STRUCTURE, RequirementKind.CUSTOM, RequirementKind.BUILD, RequirementKind.HTTP}
)
REGISTERED_KINDS = frozenset({RequirementKind.STRUCTURE, RequirementKind.CUSTOM})
"""The kinds that run a registered check; one whose `depends_on` failed is not run."""


class DeterministicCheckService:
    """Turns an assignment's structure, custom, build and http requirements into findings.

    `code` requirements are left to the LLM reviewer. A requirement that cannot be checked
    is listed as not evaluated, with its reason, instead of becoming a finding. An
    assignment's `applies_when` or `skip_when` is evaluated first: when the assignment does
    not apply to the repo, or the condition cannot be evaluated, none of its requirements
    is (ADR 0023: the path per repo is data). A structure or custom requirement whose
    `depends_on` names a requirement of the assignment checked before it that failed (or
    was not run for the same reason) is not run: it is not evaluated, with
    `dependency_failed` and the requirements it needs as the detail. With a
    `git` client, the working copy's tracked files are read once per evaluation for the
    checks that look at git's state; without one, those checks are not evaluated. With an
    `architecture` reader, the working copy's architecture evidence is read at most once
    per evaluation, and only when a check that declares it reads it runs; without one, or
    when it cannot be read, those checks are not evaluated.
    """

    def __init__(
        self,
        checker: RequirementChecker,
        git: GitClient | None = None,
        architecture: ArchitectureEvidenceReader | None = None,
    ) -> None:
        self._checker = checker
        self._git = git
        self._architecture = architecture

    def evaluate(
        self, assignment: Assignment, repo_dir: Path, execution: ExecutionResult | None
    ) -> AssignmentResult:
        """Evaluate every deterministic requirement of `assignment` against the working copy.

        When the assignment's condition says it does not apply (or cannot tell), every
        requirement is not evaluated, with `assignment_not_applicable` (or
        `assignment_condition_unknown`) and the condition's words as the detail.

        Raises:
            InvalidAssignmentError: a requirement or the condition names an unknown check or
                invalid params.
        """
        findings: list[Finding] = []
        not_evaluated: list[NotEvaluated] = []
        inputs = _Inputs(
            self._checker,
            repo_dir,
            assignment,
            execution,
            self._tracked_files(repo_dir),
            lambda: self._read_architecture(repo_dir),
        )
        skipped = self._not_applicable(assignment, inputs)
        if skipped is not None:
            reason, detail = skipped
            return AssignmentResult(
                assignment_id=assignment.id,
                not_evaluated=tuple(
                    NotEvaluated(requirement_id=requirement.id, reason=reason, detail=detail)
                    for requirement in assignment.requirements
                ),
            )
        paths = ", ".join(execution.windows_paths) if execution else ""
        blocked: set[str] = set()  # failed, or not run because one they need failed
        for requirement in assignment.requirements:
            if requirement.kind not in DETERMINISTIC_KINDS:
                continue
            needed = [item for item in requirement.depends_on if item in blocked]
            if needed and requirement.kind in REGISTERED_KINDS:
                blocked.add(requirement.id)
                not_evaluated.append(
                    NotEvaluated(
                        requirement_id=requirement.id,
                        reason=NotEvaluatedReason.DEPENDENCY_FAILED,
                        detail=_bounded(", ".join(f"`{item}`" for item in needed)),
                    )
                )
                continue
            outcome = self._check(requirement, inputs.for_requirement(requirement))
            if isinstance(outcome, NotEvaluatedReason):
                detail = paths if outcome is NotEvaluatedReason.WINDOWS_PATH else ""
                not_evaluated.append(
                    NotEvaluated(requirement_id=requirement.id, reason=outcome, detail=detail)
                )
            else:
                findings.extend(outcome)
                if outcome:
                    blocked.add(requirement.id)
        return _windows_path_dependents(
            assignment,
            AssignmentResult(
                assignment_id=assignment.id,
                findings=tuple(findings),
                not_evaluated=tuple(not_evaluated),
            ),
            paths,
        )

    def _not_applicable(
        self, assignment: Assignment, inputs: "_Inputs"
    ) -> tuple[NotEvaluatedReason, str] | None:
        """Why the assignment is not checked in this repo, with the condition's words; None
        when it applies (or has no condition)."""
        condition = assignment.condition
        requirement = assignment.condition_requirement
        if condition is None or requirement is None:
            return None
        outcome = self._condition(requirement, inputs.for_requirement(requirement))
        name = condition.describe()
        if outcome.holds is None:
            reason = outcome.reason.value if outcome.reason else "unknown"
            detail = f"{name} could not be evaluated ({reason}); check the repo by hand"
            logger.info("Assignment %s: its %s could not be evaluated", assignment.id, name)
            return NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN, _bounded(detail)
        if condition.applies(outcome.holds):
            return None
        said = outcome.message or ("holds" if outcome.holds else "does not hold")
        logger.info("Assignment %s does not apply to the repo (%s)", assignment.id, name)
        return NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE, _bounded(f"{name}: {said}")

    def _condition(self, requirement: Requirement, check_input: CheckInput) -> ConditionOutcome:
        try:
            return self._checker.condition(check_input)
        except InvalidAssignmentError:
            raise  # bad condition data stops the round; it is not the student's repo
        except Exception as error:
            # Deliberate boundary, as for a requirement: the student's repo is untrusted.
            logger.warning(
                "Condition %s failed (%s); the assignment's path is unknown",
                requirement.id,
                type(error).__name__,
                extra={"requirement_id": requirement.id},
            )
            return ConditionOutcome(holds=None, reason=NotEvaluatedReason.ENVIRONMENT_ISSUE)

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

    def _read_architecture(self, repo_dir: Path) -> ArchitectureEvidence | None:
        if self._architecture is None:
            return None
        try:
            return self._architecture.scan(repo_dir)
        except Exception as error:
            # Deliberate boundary: the reader walks an untrusted student repo; the checks
            # that need its evidence are then not evaluated, as without git's state.
            logger.warning(
                "Could not read the architecture evidence (%s); the checks that read it are "
                "not evaluated",
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


@dataclass
class _Inputs:
    """What every check of one evaluation receives. The architecture evidence is read on
    the first check that declares it reads it, and kept for the others."""

    checker: RequirementChecker
    repo_dir: Path
    assignment: Assignment
    execution: ExecutionResult | None
    tracked: TrackedFiles | None
    read_architecture: Callable[[], ArchitectureEvidence | None]
    _architecture: list[ArchitectureEvidence | None] = field(default_factory=list)
    """Empty until read; then the one value read (None when it could not be)."""

    def for_requirement(self, requirement: Requirement) -> CheckInput:
        return CheckInput(
            repo_dir=self.repo_dir,
            assignment=self.assignment,
            requirement=requirement,
            execution=self.execution,
            tracked=self.tracked,
            architecture=self._evidence() if self.checker.reads_architecture(requirement) else None,
        )

    def _evidence(self) -> ArchitectureEvidence | None:
        if not self._architecture:
            self._architecture.append(self.read_architecture())
        return self._architecture[0]


def _bounded(detail: str) -> str:
    """`detail`, cut to what a not-evaluated entry holds."""
    if len(detail) <= MAX_NOT_EVALUATED_DETAIL_CHARS:
        return detail
    return detail[: MAX_NOT_EVALUATED_DETAIL_CHARS - 1] + "…"


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
