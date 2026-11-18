"""Holds back the requirements whose `depends_on` names one that did not pass, across the
assignments of one repo, so a broken requirement is reported once.

Every probe is still sent (the runner sends them all before anything is judged); a held-back
requirement's result is replaced after all the assignments of the repo were checked.
"""

from collections.abc import Sequence

from hw_checker.domain import Assignment, AssignmentResult, NotEvaluated, NotEvaluatedReason
from hw_checker.services.deterministic_checks import bounded

CONTAINER_REASON_ORDER = (NotEvaluatedReason.WINDOWS_PATH, NotEvaluatedReason.DATABASE_UNAVAILABLE)
"""The reasons a held-back requirement takes from its dependencies (with their detail), in
the order they win; any other blocked dependency gives `dependency_failed`."""


def hold_back_dependents(
    assignments: Sequence[Assignment], results: Sequence[AssignmentResult]
) -> tuple[AssignmentResult, ...]:
    """`results`, in their order, with every held-back requirement not evaluated.

    A requirement is blocked when it has a finding or is not evaluated, for any reason. One
    whose `depends_on` names a blocked requirement of any of `assignments` (also through
    others) is held back, whatever its kind, and blocked in turn: its findings are removed;
    if it was not evaluated it keeps its reason (a `dependency_failed` without a detail gets
    the requirements it needs); otherwise it becomes not evaluated, with a dependency's
    `windows_path` or `database_unavailable` and its detail, or else `dependency_failed`
    naming the requirements it needs. A passing requirement is held back too. Only the
    requirements of assignments that have a result count; `depends_on` has no cycles (the
    loader rejects them).
    """
    holder = _HoldBack(assignments, results)
    return tuple(holder.adjusted(result) for result in results)


class _HoldBack:
    """The blocked requirements of one repo, and what each held-back one becomes."""

    def __init__(
        self, assignments: Sequence[Assignment], results: Sequence[AssignmentResult]
    ) -> None:
        checked = {result.assignment_id for result in results}
        self._ids_of = {
            assignment.id: [requirement.id for requirement in assignment.requirements]
            for assignment in assignments
            if assignment.id in checked
        }
        self._depends = {
            requirement.id: requirement.depends_on
            for assignment in assignments
            if assignment.id in checked
            for requirement in assignment.requirements
        }
        self._failed = {f.requirement_id for result in results for f in result.findings}
        self._entries = {
            entry.requirement_id: entry for result in results for entry in result.not_evaluated
        }
        self._held: dict[str, NotEvaluated | None] = {}
        """Per requirement looked at: its entry when held back, None when it is not."""

    def adjusted(self, result: AssignmentResult) -> AssignmentResult:
        """`result` with its held-back requirements not evaluated."""
        ids = self._ids_of.get(result.assignment_id, [])
        held = {item: entry for item in ids if (entry := self._held_entry(item)) is not None}
        if not held:
            return result
        listed = {entry.requirement_id for entry in result.not_evaluated}
        return result.model_copy(
            update={
                "findings": tuple(f for f in result.findings if f.requirement_id not in held),
                "not_evaluated": (
                    *(held.get(entry.requirement_id, entry) for entry in result.not_evaluated),
                    *(held[item] for item in ids if item in held and item not in listed),
                ),
            }
        )

    def _blocked(self, requirement_id: str) -> bool:
        return (
            requirement_id in self._failed
            or requirement_id in self._entries
            or self._held_entry(requirement_id) is not None
        )

    def _held_entry(self, requirement_id: str) -> NotEvaluated | None:
        if requirement_id not in self._held:
            self._held[requirement_id] = self._hold(requirement_id)
        return self._held[requirement_id]

    def _hold(self, requirement_id: str) -> NotEvaluated | None:
        needed = [
            item
            for item in self._depends.get(requirement_id, ())
            if item in self._depends and self._blocked(item)
        ]
        if not needed:
            return None
        names = bounded(", ".join(f"`{item}`" for item in needed))
        entry = self._entries.get(requirement_id)
        if entry is not None:
            if entry.reason is NotEvaluatedReason.DEPENDENCY_FAILED and not entry.detail:
                return entry.model_copy(update={"detail": names})
            return entry
        dependencies = [self._entries.get(item) or self._held_entry(item) for item in needed]
        for reason in CONTAINER_REASON_ORDER:
            for dependency in dependencies:
                if dependency is not None and dependency.reason is reason:
                    return NotEvaluated(
                        requirement_id=requirement_id, reason=reason, detail=dependency.detail
                    )
        return NotEvaluated(
            requirement_id=requirement_id, reason=NotEvaluatedReason.DEPENDENCY_FAILED, detail=names
        )
