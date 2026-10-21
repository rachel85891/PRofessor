"""Assignment catalog service: lists assignments and selects those checked in a round."""

from hw_checker.domain import Assignment
from hw_checker.domain.errors import InvalidAssignmentError, NotFoundError
from hw_checker.ports import AssignmentSource, CheckCatalog

GENERAL_ORDER = 0
"""`_general` is the assignment with order 0; it is part of every round."""


class AssignmentCatalogService:
    """Selects `_general` plus assignments 1..N, refusing requirements with invalid checks."""

    def __init__(self, source: AssignmentSource, catalog: CheckCatalog) -> None:
        self._source = source
        self._catalog = catalog

    def list_assignments(self) -> tuple[Assignment, ...]:
        """Return every assignment the source holds, ordered by `order`.

        Raises:
            InvalidAssignmentError: the assignment data cannot be loaded.
        """
        return self._source.load_all()

    def select_for_round(self, assignment_id: str) -> tuple[Assignment, ...]:
        """Return `_general` and every assignment up to `assignment_id`, sorted by `order`.

        Only the selected assignments are checked against the check catalog, so a draft of
        a later assignment cannot block the round.

        Raises:
            NotFoundError: `assignment_id` is unknown, or is `_general` itself.
            InvalidAssignmentError: the data cannot be loaded, or a selected requirement
                names an unknown check or gives it invalid params.
        """
        assignments = self._source.load_all()
        target = next((a for a in assignments if a.id == assignment_id), None)
        if target is None or target.order == GENERAL_ORDER:
            selectable = ", ".join(a.id for a in assignments if a.order != GENERAL_ORDER)
            raise NotFoundError(
                f"Unknown assignment '{assignment_id}'. "
                f"Assignments that can be checked: {selectable or 'none'}."
            )
        selected = tuple(
            sorted((a for a in assignments if a.order <= target.order), key=lambda a: a.order)
        )
        problems = self._check_problems(selected)
        if problems:
            count = f"{len(problems)} problem{'' if len(problems) == 1 else 's'}"
            raise InvalidAssignmentError(
                f"Cannot start a round for '{assignment_id}': checks in the selected "
                f"assignments are invalid ({count}).\n"
                + "\n".join(f"- {problem}" for problem in problems)
            )
        return selected

    def validate_all(self) -> tuple[str, ...]:
        """Return check problems across every assignment, without raising for them.

        Raises:
            InvalidAssignmentError: the assignment data cannot be loaded.
        """
        return self._check_problems(self._source.load_all())

    def _check_problems(self, assignments: tuple[Assignment, ...]) -> tuple[str, ...]:
        return tuple(
            f"{assignment.id}: requirement '{requirement.id}', check '{requirement.check}': "
            f"{problem}"
            for assignment in assignments
            for requirement in assignment.requirements
            if requirement.check is not None
            for problem in self._catalog.validate_params(requirement.check, requirement.params)
        )
