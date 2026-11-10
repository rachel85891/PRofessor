"""Authoring service: adds an assignment from the developer's intake (ADR 0023, §8.3)."""

import json
import logging
import re
from pathlib import Path

from hw_checker.domain import Assignment
from hw_checker.domain.authoring import AssignmentIntake, AuthorReply, DataFile
from hw_checker.domain.course import ItemStatus
from hw_checker.domain.errors import AuthoringError
from hw_checker.ports import (
    AssignmentSource,
    AuthoringFiles,
    CheckCatalog,
    LLMClient,
    TextTemplates,
)

logger = logging.getLogger(__name__)

INTAKE_TEMPLATE = "intake.yaml.j2"
AUTHOR_TEMPLATE = "author.md.j2"
AUTHOR_ROLE = "author"
_COMMENT_LINE = re.compile(r"^[ \t]*#(?: .*)?(?:\n|\Z)", re.MULTILINE)
"""A YAML comment line (`# ...` or a bare `#`), left out of the files shown to the author."""
_DATA_TAGS = ("data_file", "intake", "task_text", "previous_draft")
ASSIGNMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
"""A new assignment's id, which is also its folder's name (`hw02`)."""


class AuthoringService:
    """Creates a new assignment's folder from the intake template, and drafts its
    requirements and review rules with the `author` role.

    The author sees the task text, the intake, the course's and the earlier assignments'
    files and the check catalog; never a student's code (ADR 0023).
    """

    def __init__(
        self,
        files: AuthoringFiles,
        source: AssignmentSource,
        templates: TextTemplates,
        catalog: CheckCatalog,
        llm: LLMClient,
    ) -> None:
        self._files = files
        self._source = source
        self._templates = templates
        self._catalog = catalog
        self._llm = llm

    def load_intake(self, assignment_id: str) -> AssignmentIntake:
        """The intake of `assignment_id`, after checking its task text is there.

        Raises:
            AuthoringError: the intake or `task.md` is missing, or `task.md` is empty.
            InvalidAssignmentError: the intake is invalid.
        """
        intake = self._files.load_intake(assignment_id)
        self._files.task_text(assignment_id)
        return intake

    def draft(
        self,
        intake: AssignmentIntake,
        previous: AuthorReply | None = None,
        reasons: tuple[str, ...] = (),
    ) -> AuthorReply:
        """Ask the `author` role for the assignment's requirements and rules; every item is
        returned as a draft, whatever status the reply gave it.

        Raises:
            AuthoringError: the task text or the data files cannot be read.
            InvalidAssignmentError: the existing assignments are invalid.
            LLMOutputError: the reply could not be read as an `AuthorReply`.
            LLMError: the call failed.
        """
        prompt = self.author_prompt(intake, previous, reasons)
        reply = self._llm.complete(AUTHOR_ROLE, prompt, AuthorReply)
        logger.info(
            "The author drafted %d requirements, %d rules and %d examples for %s",
            len(reply.requirements),
            len(reply.rules),
            len(reply.examples),
            intake.id,
        )
        return as_drafts(reply)

    def author_prompt(
        self,
        intake: AssignmentIntake,
        previous: AuthorReply | None = None,
        reasons: tuple[str, ...] = (),
    ) -> str:
        """The author's prompt: the frame filled from the course, the check catalog, the
        earlier assignments' files, the intake and the task text.

        Raises:
            AuthoringError: the task text or the data files cannot be read.
            InvalidAssignmentError: the existing assignments are invalid.
        """
        course = self._source.load_course().profile
        earlier = [a.id for a in self._source.load_all() if a.order < intake.order]
        files = (*self._files.course_files(), *self._files.assignment_files(earlier))
        assignment = Assignment(
            id=intake.id, order=intake.order, title=intake.title, requirements=()
        )
        return self._templates.render(
            AUTHOR_TEMPLATE,
            {
                "course": course,
                "assignment": assignment,
                "checks": self._catalog.describe(),
                "files": [_shown(file) for file in files],
                "items": intake.items(),
                "intake": _data(intake.model_dump_json(indent=2, exclude_defaults=True)),
                "task_text": _data(self._files.task_text(intake.id).strip()),
                "previous": _data(previous.model_dump_json(indent=2)) if previous else "",
                "reasons": list(reasons),
            },
        )

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


def as_drafts(reply: AuthorReply) -> AuthorReply:
    """`reply` with every requirement, rule and example a draft: only the developer
    approves."""
    draft = {"status": ItemStatus.DRAFT}
    return reply.model_copy(
        update={
            "requirements": tuple(r.model_copy(update=draft) for r in reply.requirements),
            "rules": tuple(r.model_copy(update=draft) for r in reply.rules),
            "examples": tuple(e.model_copy(update=draft) for e in reply.examples),
        }
    )


def _shown(file: DataFile) -> DataFile:
    """`file` as the author sees it: without comment lines, as data."""
    return DataFile(label=file.label, text=_data(_COMMENT_LINE.sub("", file.text).strip()))


def _data(text: str) -> str:
    """`text` with the prompt's data tags escaped, so it cannot close its block."""
    for tag in _DATA_TAGS:
        text = text.replace(f"</{tag}>", f"<\\/{tag}>")
    return text


def _previous(intakes: tuple[AssignmentIntake, ...], order: int) -> AssignmentIntake | None:
    earlier = [intake for intake in intakes if intake.order < order]
    return max(earlier, key=lambda intake: intake.order, default=None)


def _quoted(texts: tuple[str, ...]) -> list[str]:
    """Each text as a JSON string, which YAML reads as the same text."""
    return [json.dumps(text, ensure_ascii=False) for text in texts]
