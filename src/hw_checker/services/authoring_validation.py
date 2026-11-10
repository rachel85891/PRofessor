"""The authoring flow's deterministic validation (design §8.3, stage 6): no LLM.

A draft passes when the loader and the check catalog accept it, every intake item is
covered, and nothing fails the reference solution. The preview on the workspace clones
and the rule changes are shown to the developer; they never fail a draft.
"""

import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    Course,
    ExecutionResult,
    NotEvaluatedReason,
    OriginSource,
    RequirementKind,
    new_run_id,
)
from hw_checker.domain.authoring import (
    AssignmentIntake,
    AuthorReply,
    Coverage,
    DryRun,
    DryRunStatus,
    Preview,
    PreviewCount,
    PreviewStatus,
    RuleChanges,
    ValidationReport,
)
from hw_checker.domain.course import PolicyItem, in_effect
from hw_checker.domain.errors import HwCheckerError, InvalidAssignmentError
from hw_checker.ports import AuthoringFiles, CheckCatalog, Clock
from hw_checker.services.assignments import check_problems
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService

logger = logging.getLogger(__name__)

REFERENCE_REPO_ID = "reference"
"""The repo id the dry run builds and runs the reference solution under."""
FILE_KINDS = frozenset({RequirementKind.STRUCTURE, RequirementKind.CUSTOM})
"""The requirements the preview runs without building: their checks read files."""
RUN_KINDS = frozenset({RequirementKind.BUILD, RequirementKind.HTTP})
"""The requirements the preview adds when it builds and runs (`--with-execution`)."""
NOT_A_FAILURE: frozenset[NotEvaluatedReason] = frozenset()
"""Not evaluated on the reference solution, and expected: nothing to show."""
_SPACE = re.compile(r"\s+")


class DraftValidator:
    """Validates a draft: the schemas, the catalog, the origins and the coverage; then the
    dry run on `reference/`, the preview on the workspace clones, and the rule changes.

    `execution` returns the execution service; it is called only when something is built
    and run, so a draft without a reference solution needs no Docker. The preview counts
    repos only: it never names a repo or a student.
    """

    def __init__(
        self,
        files: AuthoringFiles,
        catalog: CheckCatalog,
        checks: DeterministicCheckService,
        execution: Callable[[], ExecutionService],
        clock: Clock,
        flag_share: float,
    ) -> None:
        self._files = files
        self._catalog = catalog
        self._checks = checks
        self._execution = execution
        self._clock = clock
        self._flag_share = flag_share

    def validate(
        self, intake: AssignmentIntake, reply: AuthorReply, with_execution: bool = False
    ) -> ValidationReport:
        """Validate `reply` for `intake`; every problem is listed together.

        Raises:
            AuthoringError: the task text cannot be read.
            ExecutionEnvironmentError: Docker cannot be reached for the dry run.
        """
        task_text = self._files.task_text(intake.id)
        problems = list(_origin_problems(intake, task_text, reply))
        loaded: tuple[Course, tuple[Assignment, ...]] | None = None
        try:
            loaded = self._files.catalog_with(
                intake.id, self._files.draft_files(intake, reply, None)
            )
        except InvalidAssignmentError as error:
            problems += error.problems or (str(error),)
        target: Assignment | None = None
        selected: tuple[Assignment, ...] = ()
        if loaded is not None:
            target = next(a for a in loaded[1] if a.id == intake.id)
            selected = tuple(a for a in loaded[1] if a.order <= target.order)
            problems += check_problems((target,), self._catalog)
        coverage = _coverage(intake, reply)
        if problems or loaded is None or target is None:
            return ValidationReport(
                problems=tuple(problems),
                coverage=coverage,
                dry_run=DryRun(status=DryRunStatus.NOT_RUN),
                preview=Preview(status=PreviewStatus.NOT_RUN),
            )
        return ValidationReport(
            coverage=coverage,
            dry_run=self._dry_run(target, selected),
            preview=self._preview(target, selected, with_execution),
            changes=rule_changes(loaded[0], loaded[1], target),
        )

    def _dry_run(self, target: Assignment, selected: tuple[Assignment, ...]) -> DryRun:
        reference = self._files.reference_dir(target.id)
        if reference is None:
            logger.info("No reference solution for %s: the dry run is skipped", target.id)
            return DryRun(status=DryRunStatus.SKIPPED)
        execution = self._execution()
        execution.prepare()
        run_id = new_run_id(self._clock.now())
        result = self._checks.evaluate(
            target, reference, execution.execute(run_id, REFERENCE_REPO_ID, reference, selected)
        )
        failures = tuple(
            f"{requirement_id}: {message}"
            for requirement_id, message in _first_messages(result).items()
        )
        not_evaluated = tuple(
            f"{entry.requirement_id}: not evaluated ({entry.reason.value})"
            for entry in result.not_evaluated
            if entry.reason not in NOT_A_FAILURE
        )
        status = DryRunStatus.FAILED if failures else DryRunStatus.PASSED
        logger.info("The dry run of %s on its reference solution: %s", target.id, status.value)
        return DryRun(status=status, failures=failures, not_evaluated=not_evaluated)

    def _preview(
        self, target: Assignment, selected: tuple[Assignment, ...], with_execution: bool
    ) -> Preview:
        clones = self._files.clone_dirs()
        if not clones:
            return Preview(status=PreviewStatus.NO_REPOS, with_execution=with_execution)
        kinds = FILE_KINDS | RUN_KINDS if with_execution else FILE_KINDS
        previewed = target.model_copy(
            update={"requirements": tuple(r for r in target.requirements if r.kind in kinds)}
        )
        run = self._runner(selected) if with_execution else None
        results: list[AssignmentResult] = []
        errors = 0
        for clone in clones:
            try:
                results.append(self._checks.evaluate(previewed, clone, run(clone) if run else None))
            except HwCheckerError as error:
                errors += 1
                logger.warning("A repo could not be previewed (%s)", type(error).__name__)
        logger.info("Previewed %s on %d repos (%d errors)", target.id, len(clones), errors)
        return Preview(
            status=PreviewStatus.RAN,
            with_execution=with_execution,
            repos=len(clones),
            errors=errors,
            counts=tuple(self._count(r.id, results) for r in previewed.requirements),
            left_out=tuple(r.id for r in target.requirements if r.kind not in kinds),
        )

    def _runner(self, selected: tuple[Assignment, ...]) -> Callable[[Path], ExecutionResult]:
        execution = self._execution()
        execution.prepare()
        run_id = new_run_id(self._clock.now())
        return lambda clone: execution.execute(run_id, clone.name, clone, selected)

    def _count(self, requirement_id: str, results: Sequence[AssignmentResult]) -> PreviewCount:
        outcomes: Counter[str] = Counter()
        for result in results:
            if any(f.requirement_id == requirement_id for f in result.findings):
                outcomes["failed"] += 1
            elif any(n.requirement_id == requirement_id for n in result.not_evaluated):
                outcomes["not_evaluated"] += 1
            else:
                outcomes["passed"] += 1
        evaluated = outcomes["failed"] + outcomes["passed"]
        return PreviewCount(
            requirement_id=requirement_id,
            failed=outcomes["failed"],
            passed=outcomes["passed"],
            not_evaluated=outcomes["not_evaluated"],
            flagged=evaluated > 0 and outcomes["failed"] >= self._flag_share * evaluated,
        )


def rule_changes(
    course: Course, assignments: Sequence[Assignment], target: Assignment
) -> RuleChanges:
    """The review rules and examples in effect for `target`, against those in effect for
    the assignment before it (by order): new, replacing an earlier one, and ended."""
    earlier = [a for a in assignments if a.order < target.order]
    if not earlier:
        return RuleChanges()
    previous = max(earlier, key=lambda a: a.order)
    before = {item.id for item in _in_effect(course, assignments, previous)}
    now = _in_effect(course, assignments, target)
    replaced = [item for item in now if item.replaces in before and item.id not in before]
    taken = {item.replaces for item in replaced}
    return RuleChanges(
        new=tuple(item.id for item in now if item.id not in before and item.replaces not in before),
        replaced=tuple(f"{item.id} (replaces {item.replaces})" for item in replaced),
        ended=tuple(
            item_id
            for item_id in sorted(before)
            if item_id not in {item.id for item in now} and item_id not in taken
        ),
    )


def _in_effect(
    course: Course, assignments: Sequence[Assignment], checked: Assignment
) -> list[PolicyItem]:
    layers = [a for a in sorted(assignments, key=lambda a: a.order) if a.order <= checked.order]
    passed = {a.id: a.order < checked.order for a in assignments}
    rules = in_effect([course.rules.rules, *(a.review_rules.rules for a in layers)], passed)
    examples = in_effect(
        [course.rules.examples, *(a.review_rules.examples for a in layers)], passed
    )
    return [*rules, *examples]


def _coverage(intake: AssignmentIntake, reply: AuthorReply) -> tuple[Coverage, ...]:
    return tuple(
        Coverage(
            key=key,
            text=text,
            covered_by=tuple(
                item.id
                for item in reply.items
                if key in item.intake_items
                or (item.origin.source is OriginSource.INTAKE and item.origin.item == key)
            ),
        )
        for key, text in intake.items()
    )


def _origin_problems(intake: AssignmentIntake, task_text: str, reply: AuthorReply) -> list[str]:
    """Every item's origin says where it comes from truthfully: a task-text quote is in
    `task.md`, an intake item exists; and every covered key is an intake item."""
    keys = {key for key, _ in intake.items()}
    task = _normalized(task_text)
    problems: list[str] = []
    for item in reply.items:
        origin = item.origin
        where = f"{type(item).__name__.removeprefix('Drafted').lower()} '{item.id}'"
        if origin.source is OriginSource.TASK_TEXT and (
            not origin.quote.strip() or _normalized(origin.quote) not in task
        ):
            problems.append(
                f"{where}: origin task_text, but its quote is not in task.md; copy the "
                "words exactly, or use inferred"
            )
        if origin.source is OriginSource.INTAKE and origin.item not in keys:
            problems.append(
                f"{where}: origin intake, but '{origin.item}' is not an intake item's key"
            )
        if origin.source is OriginSource.DEVELOPER:
            problems.append(f"{where}: origin developer is for hand-written items only")
        problems += [
            f"{where}: intake_items names '{key}', which is not an intake item's key"
            for key in item.intake_items
            if key not in keys
        ]
    return problems


def _first_messages(result: AssignmentResult) -> dict[str, str]:
    messages: dict[str, str] = {}
    for finding in result.findings:
        first_line = finding.message_en.strip().splitlines()[0] if finding.message_en else ""
        messages.setdefault(finding.requirement_id, first_line or "a finding")
    return messages


def _normalized(text: str) -> str:
    return _SPACE.sub(" ", text).strip().casefold()
