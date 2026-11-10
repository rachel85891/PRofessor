"""Authoring service: adds an assignment from the developer's intake (ADR 0023, §8.3)."""

import json
import logging
import re
from pathlib import Path

from hw_checker.domain.authoring import AssignmentIntake
from hw_checker.domain.errors import AuthoringError
from hw_checker.ports import AssignmentSource, AuthoringFiles, TextTemplates

logger = logging.getLogger(__name__)

INTAKE_TEMPLATE = "intake.yaml.j2"
ASSIGNMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
"""A new assignment's id, which is also its folder's name (`hw02`)."""


class AuthoringService:
    """Creates a new assignment's folder from the intake template."""

    def __init__(
        self, files: AuthoringFiles, source: AssignmentSource, templates: TextTemplates
    ) -> None:
        self._files = files
        self._source = source
        self._templates = templates

    def new_assignment(self, assignment_id: str, order: int | None = None) -> Path:
        """Create `assignments/<id>/` with a commented `intake.yaml` and an empty `task.md`.

        `order` defaults to one after the highest order of the existing assignments and
        intakes. `taught` and `not_yet_taught` start as the previous assignment's (the
        intake with the highest order below the new one), when it has an intake.

        Raises:
            AuthoringError: the id is not a folder name, or its folder exists.
            InvalidAssignmentError: the existing assignments or intakes are invalid.
        """
        if not ASSIGNMENT_ID.match(assignment_id):
            raise AuthoringError(
                f"'{assignment_id}' cannot be an assignment id: use letters, digits, '_' "
                "and '-', starting with a letter (for example hw02)."
            )
        if self._files.exists(assignment_id):
            raise AuthoringError(
                f"The assignment '{assignment_id}' already exists; choose another id."
            )
        intakes = self._files.intakes()
        orders = [a.order for a in self._source.load_all()] + [i.order for i in intakes]
        new_order = order if order is not None else max(orders, default=0) + 1
        previous = _previous(intakes, new_order)
        text = self._templates.render(
            INTAKE_TEMPLATE,
            {
                "id": assignment_id,
                "order": new_order,
                "previous": previous.id if previous else "",
                "taught": _quoted(previous.taught if previous else ()),
                "not_yet_taught": _quoted(previous.not_yet_taught if previous else ()),
            },
        )
        folder = self._files.create(assignment_id, text)
        logger.info("New assignment %s (order %d) in %s", assignment_id, new_order, folder)
        return folder


def _previous(intakes: tuple[AssignmentIntake, ...], order: int) -> AssignmentIntake | None:
    earlier = [intake for intake in intakes if intake.order < order]
    return max(earlier, key=lambda intake: intake.order, default=None)


def _quoted(texts: tuple[str, ...]) -> list[str]:
    """Each text as a JSON string, which YAML reads as the same text."""
    return [json.dumps(text, ensure_ascii=False) for text in texts]
