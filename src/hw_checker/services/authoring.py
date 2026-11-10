"""Authoring service: adds an assignment from the developer's intake (ADR 0023, §8.3)."""

import json
import logging
import re
from pathlib import Path

from hw_checker.domain import Assignment, OriginSource
from hw_checker.domain.authoring import (
    AssignmentIntake,
    AuthorReply,
    DataFile,
    DraftedExample,
    DraftedRequirement,
    DraftedRule,
    DraftFiles,
    ValidationReport,
)
from hw_checker.domain.course import ItemStatus
from hw_checker.domain.errors import AuthoringError
from hw_checker.ports import (
    AssignmentSource,
    AuthoringFiles,
    CheckCatalog,
    Clock,
    LLMClient,
    TextTemplates,
)
from hw_checker.services.authoring_validation import DraftValidator

logger = logging.getLogger(__name__)

INTAKE_TEMPLATE = "intake.yaml.j2"
AUTHOR_TEMPLATE = "author.md.j2"
REPORT_TEMPLATE = "authoring_report.md.j2"
AUTHOR_ROLE = "author"
_COMMENT_LINE = re.compile(r"^[ \t]*#(?: .*)?(?:\n|\Z)", re.MULTILINE)
"""A YAML comment line (`# ...` or a bare `#`), left out of the files shown to the author."""
_DATA_TAGS = ("data_file", "intake", "task_text", "previous_draft")
ASSIGNMENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
"""A new assignment's id, which is also its folder's name (`hw02`)."""


class AuthoringService:
    """Adds an assignment (design §8.3): creates its folder from the intake template,
    drafts its requirements and review rules with the `author` role, validates the draft,
    writes the developer's report, and writes the files as a draft, then approved.

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
        validator: DraftValidator,
        clock: Clock,
    ) -> None:
        self._files = files
        self._source = source
        self._templates = templates
        self._catalog = catalog
        self._llm = llm
        self._validator = validator
        self._clock = clock

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

    def validate(
        self, intake: AssignmentIntake, reply: AuthorReply, with_execution: bool = False
    ) -> ValidationReport:
        """Validate the draft deterministically (`DraftValidator.validate`).

        Raises:
            AuthoringError: the task text cannot be read.
            ExecutionEnvironmentError: Docker cannot be reached for the dry run.
        """
        return self._validator.validate(intake, reply, with_execution)

    def write_report(
        self,
        intake: AssignmentIntake,
        reply: AuthorReply,
        report: ValidationReport,
        attempt: int,
        max_attempts: int,
        failed: bool = False,
    ) -> Path:
        """Write `authoring_report.md` (and the draft as JSON) to the private authoring
        folder; return the report's path. `failed` says the last attempt still failed.

        Raises:
            AuthoringError: the report cannot be written.
        """
        assignment = Assignment(
            id=intake.id, order=intake.order, title=intake.title, requirements=()
        )
        text = self._templates.render(
            REPORT_TEMPLATE,
            {
                "assignment": assignment,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "stopped": failed,
                "report": report,
                "items": _report_items(reply, report),
                "suggestions": list(reply.frame_suggestions),
            },
        )
        path = self._files.write_report(intake.id, text, reply)
        logger.info("The authoring report of %s is at %s", intake.id, path)
        return path

    def stage(self, intake: AssignmentIntake, reply: AuthorReply) -> DraftFiles:
        """Write the validated draft to the assignment's folder with `status: draft`, so a
        round refuses the assignment until it is approved; return what was written.

        Raises:
            AuthoringError: the files cannot be written.
        """
        files = self._files.draft_files(intake, as_drafts(reply), None)
        self._files.write_files(intake.id, files)
        return files

    def approve(
        self, intake: AssignmentIntake, reply: AuthorReply, staged: DraftFiles
    ) -> tuple[Path, ...]:
        """Write the draft with every item `status: approved`, the origins kept and the
        approval day in the first comment line; return the paths written.

        Raises:
            AuthoringError: the drafted files were changed by hand since they were
                written (nothing is approved), or cannot be written.
        """
        if self._files.read_files(intake.id) != staged:
            raise AuthoringError(
                f"The drafted files of {intake.id} were changed since the report was "
                "written, so they were not approved. Say what to change with `revise "
                "--note`, or restore the files and approve again."
            )
        files = self._files.draft_files(
            intake, with_status(reply, ItemStatus.APPROVED), self._clock.now().date()
        )
        written = self._files.write_files(intake.id, files)
        logger.info("Approved %s: %s", intake.id, ", ".join(str(path) for path in written))
        return written

    def stop(self, assignment_id: str, staged: DraftFiles | None) -> bool:
        """Remove the drafted files written for the approval (`staged`), so nothing of the
        draft stays in the assignment's folder. Files changed by hand since are left as
        they are; return False then.

        Raises:
            AuthoringError: the files cannot be read or removed.
        """
        current = self._files.read_files(assignment_id)
        if staged is None or current is None:
            return True
        if current != staged:
            logger.warning("The drafted files of %s were changed by hand; kept", assignment_id)
            return False
        self._files.remove_files(assignment_id)
        return True

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
    return with_status(reply, ItemStatus.DRAFT)


def with_status(reply: AuthorReply, status: ItemStatus) -> AuthorReply:
    """`reply` with every requirement, rule and example in `status`."""
    update = {"status": status}
    return reply.model_copy(
        update={
            "requirements": tuple(r.model_copy(update=update) for r in reply.requirements),
            "rules": tuple(r.model_copy(update=update) for r in reply.rules),
            "examples": tuple(e.model_copy(update=update) for e in reply.examples),
        }
    )


def _report_items(reply: AuthorReply, report: ValidationReport) -> list[dict[str, object]]:
    """Every drafted item for the report, the inferred ones first, with its flags."""
    rows: list[dict[str, object]] = []
    for item in reply.items:
        inferred = item.origin.source is OriginSource.INFERRED
        flags = ["inferred: confirm with the lecturer"] if inferred else []
        if report.dry_run.failed(item.id):
            flags.append("fails the reference solution")
        count = report.preview.count(item.id)
        if count is not None and count.flagged:
            flags.append(f"fails {count.failed} of {count.failed + count.passed} repos")
        rows.append(
            {
                "id": item.id,
                "what": _what(item),
                "summary": item.summary,
                "origin": _origin_text(item),
                "flags": flags,
                "inferred": inferred,
            }
        )
    return sorted(rows, key=lambda row: not row["inferred"])


def _what(item: DraftedRequirement | DraftedRule | DraftedExample) -> str:
    if isinstance(item, DraftedRequirement):
        return f"a {item.severity.value} {item.kind.value} requirement"
    if isinstance(item, DraftedRule):
        roles = " and ".join(role.value for role in item.applies_to)
        return f"a {item.kind.value} rule for the {roles}"
    return "a fixer example"


def _origin_text(item: DraftedRequirement | DraftedRule | DraftedExample) -> str:
    origin = item.origin
    if origin.source is OriginSource.TASK_TEXT:
        return f'the task text: "{origin.quote}"'
    if origin.source is OriginSource.INTAKE:
        quote = f': "{origin.quote}"' if origin.quote else ""
        return f"the intake, `{origin.item}`{quote}"
    reason = f" ({origin.quote})" if origin.quote else ""
    return f"inferred by the author{reason}"


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
