"""Course policy as data (ADR 0023, design §8.1): the course profile and the review rules.

Code holds the mechanism (detectors, guardrails, the prompts' frames); every teaching
decision is one of these models, loaded from `course/` and the assignments' folders. One
model per file: `CourseProfile` is `course/course.yaml`, `ReviewRuleSet` is a
`review_rules.yaml`. Their JSON Schemas are generated from the models.
"""

import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr
from hw_checker.domain.fixes import NoteSection


class ReviewRole(StrEnum):
    """The LLM roles a rule speaks to; each prompt gets only its role's rules."""

    REVIEWER = "reviewer"
    FIXER = "fixer"
    JUDGE = "judge"
    WRITER = "writer"


class RuleKind(StrEnum):
    """What a rule decides."""

    FIX_ALLOWED = "fix_allowed"
    """What may be changed in the student's code."""
    NOTE_ONLY = "note_only"
    """What may only be pointed out in a comment."""
    NEVER_DO = "never_do"
    """What must never be added or changed."""
    NEVER_MENTION = "never_mention"
    """What must not even be commented on."""
    STYLE = "style"
    """How comments and text are written."""


class ItemStatus(StrEnum):
    """A drafted item waits for the developer's approval; a round refuses drafts (§8.3)."""

    DRAFT = "draft"
    APPROVED = "approved"


class OriginSource(StrEnum):
    """Where an item comes from."""

    TASK_TEXT = "task_text"
    INTAKE = "intake"
    INFERRED = "inferred"
    """Drafted by the LLM without a source: to check, or to confirm with the lecturer."""
    DEVELOPER = "developer"
    """Written by hand by the developer; the commit is the approval."""


class Origin(DomainModel):
    """The source of an item, and the words it is based on."""

    source: OriginSource
    quote: str = ""
    item: str = ""
    """For an `intake` origin: the key of the intake item (`requirements.return_values.1`)."""


class SafeFix(StrEnum):
    """The registered detectors of small safe fixes. Each recognizes one shape of edit in
    code; a `fix_allowed` rule chooses which of them the judge may never report."""

    MISSING_BREAK = "missing_break"
    ID_ASSIGNMENT = "id_assignment"
    READONLY_FIELD = "readonly_field"
    STRAY_USING = "stray_using"
    CONTROLLER_SUFFIX = "controller_suffix"
    FINAL_NEWLINE = "final_newline"
    CLIENT_REFERENCE = "client_reference"
    """One `src`, `href` or `id` value, or the id a script looks up, that a finding names."""
    ASSIGNMENT_IN_CONDITION = "assignment_in_condition"
    """`=` changed to `===` in an `if` condition, and nothing else on the line."""


class RulePlace(StrEnum):
    """Where the fixer's prompt shows a rule."""

    RULES = "rules"
    """In the numbered review rules, which the self-review quotes by number."""
    FIX_KIND = "fix_kind"
    """With the `fix` edit kind, as a condition every fix meets; not numbered."""


class PrSectionId(StrEnum):
    """The PR body's sections the code fills: the writer's three lists, and the plan's
    notes on leftover files."""

    FIXED = "fixed"
    FOR_YOU = "for_you"
    NAMES = "names"
    FILES = "files"


WRITER_SECTIONS = (PrSectionId.FIXED, PrSectionId.FOR_YOU, PrSectionId.NAMES)
"""The sections the writer fills (the `PullRequestText` lists)."""

SHORT_NAME = "{short_name}"
"""In a title, commit message or rule: the assignment's short name (`HW1`)."""
TAUGHT_SO_FAR = "{taught_so_far}"
"""In a rule: the course's `taught_so_far` phrase, or nothing when nothing is listed."""
NOTES_ONLY = "{notes_only}"
"""In a rule: the failing requirements that allow notes only (`feedback: notes_only`), as
`` `id` `` items; a rule that holds it is left out when there are none."""
LANGUAGES = "{languages}"
"""In a PR section's heading: the languages of the section's edited files (`C#`)."""
FIX_MARKERS = "{fix_markers}"
"""In a PR section's heading: the fix markers of the section's edited files (`` `//fix:` ``)."""
RULE_REFERENCE = re.compile(r"\{rule:([^{}\s]+)\}")
"""In a rule's texts: `{rule:<id>}` becomes that rule's number in the role's list."""

FileSuffix = Annotated[str, StringConstraints(pattern=r"^\.\w+$")]
"""A file type, by its suffix (`.cs`)."""


def covers(files: Sequence[str], path: str) -> bool:
    """Whether an item limited to the file types `files` speaks of the file at `path`; an
    item limited to none speaks of every file."""
    return not files or any(path.endswith(suffix) for suffix in files)


class ForbiddenText(DomainModel):
    """Text an edit must never add, checked by the guardrail before any edit is applied.

    Exactly one of `text` (literal) and `pattern` (a regular expression) is given. By
    default an added line that contains it is rejected; with `new_only`, only an edit
    that adds an occurrence (the replacement holds more than the original).
    """

    text: str = ""
    pattern: str = ""
    ignore_case: bool = False
    new_only: bool = False
    message: NonBlankStr
    """Why the edit is rejected; fed back to the fixer."""

    @model_validator(mode="after")
    def _one_of_text_and_pattern(self) -> Self:
        if bool(self.text) == bool(self.pattern):
            raise ValueError("give exactly one of text and pattern")
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ValueError(f"pattern is not a valid regular expression ({error})") from error
        return self

    @property
    def regex(self) -> re.Pattern[str]:
        """`text` or `pattern` as a compiled expression."""
        source = self.pattern or re.escape(self.text)
        return re.compile(source, re.IGNORECASE if self.ignore_case else 0)


class ApprovedNote(DomainModel):
    """A note in the approved wording: the judge never reports a note that contains
    `words` (compared ignoring case)."""

    words: NonBlankStr
    what: NonBlankStr
    """What the note is, for the repo report."""


class RuleExample(DomainModel):
    """One edit that follows the rule, shown with the examples."""

    file: NonBlankStr
    find: str
    replace: str


class RuleWording(DomainModel):
    """A rule's text for a prompt whose edited file types are all in `files`."""

    files: tuple[FileSuffix, ...] = Field(min_length=1)
    text: NonBlankStr


class PolicyItem(DomainModel):
    """What rules and examples share: the roles they speak to, the file types they speak
    of, how they regress from one assignment to the next, and their approval."""

    id: Identifier
    applies_to: tuple[ReviewRole, ...] = Field(min_length=1)
    files: tuple[FileSuffix, ...] = ()
    """The file types this item is limited to: it is in a prompt only when the prompt shows
    a file of one of them, and its guardrails apply only to edits in them. Empty: every
    file type."""
    when_found: tuple[Identifier, ...] = ()
    """The requirements this item is about: it is in a prompt only when one of them has a
    finding. Empty: always."""
    until: Identifier | None = None
    """The last assignment this item applies to; later assignments do not see it."""
    replaces: Identifier | None = None
    """An earlier item this one changes: it takes that item's place."""
    after: Identifier | None = None
    """An earlier item this one follows in the list; without it, the item comes after the
    items of the course and of the earlier assignments."""
    origin: Origin
    status: ItemStatus

    def covers(self, path: str) -> bool:
        """Whether this item speaks of the file at `path`."""
        return covers(self.files, path)


class ReviewRule(PolicyItem):
    """One review rule. `text` is the rule as the prompts number it; the other fields are
    what the mechanism reads from it."""

    kind: RuleKind
    text: NonBlankStr
    note_section: NoteSection | None = None
    """For a note-only rule, the PR section its notes belong in."""
    wordings: tuple[RuleWording, ...] = ()
    """The rule's text in a prompt whose edited file types (those with a comment style) are
    all in one wording's `files`: the first such wording replaces `text`."""
    shown_in: RulePlace = RulePlace.RULES
    example: RuleExample | None = None
    detectors: tuple[SafeFix, ...] = ()
    """For `fix_allowed`: the safe fixes this rule allows outright."""
    named_by: tuple[Identifier, ...] = ()
    """For a detector that changes a name (`client_reference`): the requirements whose
    findings must name what the fix changes; without such a finding the fix is rejected."""
    approved_notes: tuple[ApprovedNote, ...] = ()
    forbidden_text: tuple[ForbiddenText, ...] = ()
    forbidden_change: tuple[ForbiddenText, ...] = ()
    """Text a fix must never remove or change: checked on the lines it removes."""
    never_a_violation: NonBlankStr | None = None
    """For the judge: what this rule allows outright, as one line."""
    clarification: NonBlankStr | None = None
    """For the judge: how to read this rule, as one paragraph."""

    @model_validator(mode="after")
    def _detectors_only_allow_fixes(self) -> Self:
        if self.detectors and self.kind is not RuleKind.FIX_ALLOWED:
            raise ValueError("only a fix_allowed rule names detectors")
        if self.named_by and not self.detectors:
            raise ValueError("named_by goes with the detectors it limits")
        if self.note_section is not None and self.kind is not RuleKind.NOTE_ONLY:
            raise ValueError("note_section is only for a note_only rule")
        return self

    @model_validator(mode="after")
    def _fix_kind_rules_speak_to_the_fixer(self) -> Self:
        if self.shown_in is RulePlace.FIX_KIND and self.applies_to != (ReviewRole.FIXER,):
            raise ValueError("a rule shown with the fix kind applies to the fixer only")
        return self

    @property
    def texts(self) -> tuple[str, ...]:
        """Every text of the rule that may hold placeholders."""
        wordings = tuple(wording.text for wording in self.wordings)
        return tuple(
            text
            for text in (self.text, *wordings, self.never_a_violation, self.clarification)
            if text
        )

    def text_for(self, edited: Sequence[str]) -> str:
        """The rule's text in a prompt whose edited file types are `edited` (suffixes)."""
        return next(
            (
                wording.text
                for wording in self.wordings
                if all(suffix in wording.files for suffix in edited)
            ),
            self.text,
        )


class FixExample(PolicyItem):
    """An invented situation and the plan that handles it, in the style of the approved
    PRs. `plan` is shown exactly as written: the reply's JSON."""

    situation: NonBlankStr
    plan: NonBlankStr


class ReviewRuleSet(DomainModel):
    """One `review_rules.yaml`: the course's rules, or one assignment's."""

    rules: tuple[ReviewRule, ...] = ()
    examples: tuple[FixExample, ...] = ()

    @property
    def drafts(self) -> tuple[str, ...]:
        """The items still waiting for approval, as `rule 'x'` or `example 'x'`."""
        return tuple(
            f"{label} '{item.id}'"
            for label, items in (("rule", self.rules), ("example", self.examples))
            for item in items
            if item.status is ItemStatus.DRAFT
        )


class CommentStyle(DomainModel):
    """How a comment is written in one file type: `//Text` and `//fix: Text` in C#."""

    language: NonBlankStr
    """The file type's language, as the PR text names it (`C#`)."""
    line: NonBlankStr
    """What starts a comment line."""
    fix_marker: NonBlankStr
    """What starts the line that marks a code change; it starts with `line`."""
    end: str = ""
    """What closes a comment line, for languages that need it."""

    @model_validator(mode="after")
    def _marker_is_a_comment(self) -> Self:
        if not self.fix_marker.startswith(self.line):
            raise ValueError("fix_marker must start with the comment's line")
        return self

    @property
    def marker_pattern(self) -> re.Pattern[str]:
        """The fix marker at the start of a stripped line, allowing spaces after the
        comment's start and ignoring case."""
        rest = self.fix_marker.removeprefix(self.line)
        prefix = rf"^{re.escape(self.line)}\s*{re.escape(rest)}"
        end = re.escape(self.end)
        return re.compile(
            prefix + (rf"(?:(?!{end}).)*{end}\s*$" if self.end else ""), re.IGNORECASE
        )

    @property
    def marker_shown(self) -> str:
        """The fix marker as the PR text shows it: `//fix:`, or `<!--fix: … -->` when the
        comment needs an end."""
        return f"{self.fix_marker} … {self.end}" if self.end else self.fix_marker


class PrSection(DomainModel):
    """One section of the PR body, in the order the body shows it."""

    id: PrSectionId
    heading: NonBlankStr
    """The bold heading in the body."""
    label: NonBlankStr | None = None
    """The section's short name in the writer's prompt (writer's sections only)."""
    intro: NonBlankStr | None = None
    """What the writer puts in the section (writer's sections only)."""

    @model_validator(mode="after")
    def _writer_sections_are_described(self) -> Self:
        if self.id in WRITER_SECTIONS and (self.label is None or self.intro is None):
            raise ValueError(f"the {self.id} section needs a label and an intro for the writer")
        return self


class TitleRule(DomainModel):
    """The PR title. Code decides which applies: `strong` when the verified fix branch
    clears a major or critical requirement that failed before, `normal` otherwise."""

    strong: NonBlankStr
    normal: NonBlankStr


class PrFormat(DomainModel):
    """The review PR's text: its opening, sections, title and commit message."""

    opening: NonBlankStr
    """How the body starts (`Hi! `); added when the writer's opening lacks it."""
    opening_intro: NonBlankStr
    """What the writer puts in the opening."""
    item_rule: NonBlankStr
    """How the writer writes every item."""
    sections: tuple[PrSection, ...]
    title_rule: TitleRule
    commit_message: NonBlankStr

    @field_validator("sections")
    @classmethod
    def _every_section_once(cls, sections: tuple[PrSection, ...]) -> tuple[PrSection, ...]:
        ids = [section.id for section in sections]
        missing = [section.value for section in PrSectionId if section not in ids]
        repeated = sorted({section.value for section in ids if ids.count(section) > 1})
        if missing or repeated:
            raise ValueError(
                "every section once: "
                + "; ".join(
                    part
                    for part in (
                        f"missing {', '.join(missing)}" if missing else "",
                        f"repeated {', '.join(repeated)}" if repeated else "",
                    )
                    if part
                )
            )
        return sections

    def section(self, section_id: PrSectionId) -> PrSection:
        """The section with `section_id` (every section is there)."""
        return next(section for section in self.sections if section.id == section_id)


class CourseProfile(DomainModel):
    """`course/course.yaml`: who the students are and how the review speaks to them."""

    audience: NonBlankStr
    """Whose homework it is, as the prompts say it (`a second-year student`)."""
    level: NonBlankStr
    stack: NonBlankStr
    student_wording: NonBlankStr
    """How every student-facing text is written."""
    taught_so_far: str = ""
    """What `{taught_so_far}` in a rule becomes when the assignment lists taught
    techniques; `{techniques}` is the list."""
    comment_style: dict[str, CommentStyle] = Field(min_length=1)
    """By file suffix (`.cs`), in the order the prompts and the PR list them. A file the
    fixer is shown is edited only when its suffix is here (except a data file's newline)."""
    pr_format: PrFormat

    @field_validator("comment_style")
    @classmethod
    def _suffixes(cls, styles: dict[str, CommentStyle]) -> dict[str, CommentStyle]:
        wrong = [suffix for suffix in styles if not re.fullmatch(r"\.\w+", suffix)]
        if wrong:
            raise ValueError(f"keys are file suffixes such as .cs, not {', '.join(wrong)}")
        return styles

    def style_for(self, path: str) -> CommentStyle | None:
        """The comment style of the file at `path`; None when it is not edited."""
        return next(
            (style for suffix, style in self.comment_style.items() if path.endswith(suffix)),
            None,
        )


class Course(DomainModel):
    """The course profile and the course's own review rules."""

    profile: CourseProfile
    rules: ReviewRuleSet = ReviewRuleSet()


def in_effect[ItemT: PolicyItem](
    layers: Sequence[Sequence[ItemT]], until_passed: Mapping[str, bool]
) -> list[ItemT]:
    """The items in effect, in order, from `layers` (the course's, then each assignment's up
    to the checked one, by `order`).

    An item that `replaces` an earlier one takes its place, one placed `after` an earlier
    one follows it, and the others follow the earlier layers. Then every item whose
    `until` assignment is already passed (`until_passed[id]`) is dropped.
    """
    placed: list[ItemT] = []
    for layer in layers:
        for item in layer:
            ids = [existing.id for existing in placed]
            if item.replaces in ids:
                placed[ids.index(item.replaces)] = item
            elif item.after in ids:
                placed.insert(ids.index(item.after) + 1, item)
            else:
                placed.append(item)
    return [item for item in placed if not until_passed.get(item.until or "", False)]
