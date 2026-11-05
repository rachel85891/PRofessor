"""Assignment catalog service: lists assignments and selects those checked in a round."""

from hw_checker.domain import Assignment
from hw_checker.domain.errors import InvalidAssignmentError, NotFoundError
from hw_checker.ports import AssignmentSource, CheckCatalog

GENERAL_ORDER = 0
"""`_general` is the assignment with order 0; it is part of every round."""
COURSE_LABEL = "course"


class AssignmentCatalogService:
    """Selects `_general` plus assignments 1..N, refusing requirements with invalid checks
    and review rules that are still drafts."""

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

        Only the selected assignments are checked against the check catalog and for drafts,
        so a draft of a later assignment cannot block the round.

        Raises:
            NotFoundError: `assignment_id` is unknown, or is `_general` itself.
            InvalidAssignmentError: the data cannot be loaded, a selected requirement
                names an unknown check or gives it invalid params, or a review rule or
                example of the course or of a selected assignment is still a draft.
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
        drafts = self._drafts(selected)
        if drafts:
            raise InvalidAssignmentError(
                f"Cannot start a round for '{assignment_id}': these items are drafts; approve "
                "them first (status: approved, design §8.3).\n"
                + "\n".join(f"- {draft}" for draft in drafts)
            )
        return selected

    def validate_all(self) -> tuple[str, ...]:
        """Return check problems across every assignment, without raising for them.

        Raises:
            InvalidAssignmentError: the assignment data cannot be loaded.
        """
        return self._check_problems(self._source.load_all())

    def _drafts(self, selected: tuple[Assignment, ...]) -> list[str]:
        sets = [(COURSE_LABEL, self._source.load_course().rules)]
        sets += [(assignment.id, assignment.review_rules) for assignment in selected]
        return [f"{label}: {draft}" for label, rules in sets for draft in rules.drafts]

    def _check_problems(self, assignments: tuple[Assignment, ...]) -> tuple[str, ...]:
        problems = [
            f"{assignment.id}: requirement '{requirement.id}', check '{requirement.check}': "
            f"{problem}"
            for assignment in assignments
            for requirement in assignment.requirements
            if requirement.check is not None
            for problem in self._catalog.validate_params(requirement.check, requirement.params)
        ]
        for assignment in assignments:
            problems.extend(self._end_problems(assignment))
        return tuple(problems)

    def _end_problems(self, assignment: Assignment) -> list[str]:
        """`ends_when` names a known check that reads file names only, with valid params."""
        end = assignment.ends_when
        if end is None:
            return []
        prefix = f"{assignment.id}: ends_when, check '{end.check}'"
        problems = self._catalog.validate_params(end.check, end.params)
        if problems:
            return [f"{prefix}: {problem}" for problem in problems]
        file_list_checks = {d.name for d in self._catalog.describe() if d.reads_file_list}
        if end.check not in file_list_checks:
            return [
                f"{prefix}: this check reads file contents; ends_when needs a check that "
                f"looks at file names only ({', '.join(sorted(file_list_checks)) or 'none'})"
            ]
        return []
