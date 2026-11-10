"""Deterministic guardrails for the fixer's edits (ADR 0021, docs/fix-pr-workflow.md §3).

The model only plans edits; these rules decide which are applied. Each edit is checked on
its own, in plan order, against the files as the earlier edits left them, and a rejected
edit keeps its reason for the next attempt. An applied edit replaces exactly one
occurrence of its text, so nothing outside the edited lines changes; every file keeps its
byte-order mark and line endings. What may never be added, and how comments are written
in each edited file type, is the course's data (ADR 0023).
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hw_checker.domain import (
    CommentStyle,
    EditApplication,
    EditKind,
    ForbiddenText,
    NoteSection,
    PlannedEdit,
    RejectedEdit,
)
from hw_checker.services.fix_review import Allowances, detector_problem
from hw_checker.services.review_policy import Scoped

MIN_REASON_CHARS = 8
"""A shorter `reason` is a placeholder, not the one sentence the plan asks for."""
PLACEHOLDER_REASONS = frozenset(
    {"placeholder", "no reason", "not applicable", "see above", "same as above", "to be added"}
)
"""Longer reasons that still only fill the field (compared lowercased, without a final
period)."""
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class EditRules:
    """The configurable limits of the guardrails."""

    max_edits: int
    """At most this many edits are applied per repo; later ones are rejected."""
    max_fix_lines: int
    """A `fix` changes at most this many code lines."""
    styles: Mapping[str, CommentStyle]
    """The comment style by file suffix; only these files are edited."""
    editable: frozenset[str] | None = None
    """The files the fixer was shown; when given, no other file is edited."""
    forbidden: tuple[Scoped[ForbiddenText], ...] = ()
    """Text an edit must never add (the fixer's rules in effect), by the file types its rule
    is limited to."""
    forbidden_changes: tuple[Scoped[ForbiddenText], ...] = ()
    """Text a fix must never remove or change, by the file types its rule is limited to."""
    data_files: frozenset[str] = field(default_factory=frozenset)
    """The repo's data files, the only ones a `final_newline` edit may change."""
    allowances: Allowances | None = None
    """The safe fixes in effect: a `fix` of a safe fix's shape that its detector still
    refuses is rejected with the detector's reason."""
    notes_only: frozenset[str] = field(default_factory=frozenset)
    """The ids of the notes-only requirements (`feedback: notes_only`) and of their
    findings: a `fix` that claims one is rejected."""
    notes_only_files: frozenset[str] = field(default_factory=frozenset)
    """The files only notes-only findings point to: a `fix` there that claims nothing is
    rejected."""

    def style(self, path: str) -> CommentStyle | None:
        """The comment style of the file at `path`; None when it is not edited."""
        return next((style for suffix, style in self.styles.items() if path.endswith(suffix)), None)


@dataclass(frozen=True)
class AppliedEdits:
    """The changed files' new bytes, and what was applied or rejected."""

    files: dict[str, bytes]
    application: EditApplication


def apply_edits(
    files: Mapping[str, bytes], edits: Sequence[PlannedEdit], rules: EditRules
) -> AppliedEdits:
    """Apply the `edits` that pass every guardrail to `files` (repo-relative path → bytes).

    Returns only the files that changed. Nothing is written here.
    """
    texts: dict[str, str] = {}
    applied: list[PlannedEdit] = []
    rejected: list[RejectedEdit] = []
    for edit in edits:
        if len(applied) >= rules.max_edits:
            reason: str | None = f"more than {rules.max_edits} edits in one repo"
        else:
            reason = _apply(edit, files, texts, rules)
        if reason is None:
            applied.append(edit)
        else:
            rejected.append(RejectedEdit(edit=edit, reason=reason))
    changed = {path: text.encode("utf-8") for path, text in texts.items()}
    changed = {path: data for path, data in changed.items() if data != files[path]}
    return AppliedEdits(
        files=changed,
        application=EditApplication(
            applied=tuple(applied), rejected=tuple(rejected), changed_files=tuple(sorted(changed))
        ),
    )


def fallback_edit(fix: PlannedEdit, style: CommentStyle) -> PlannedEdit | None:
    """The note that replaces a dropped `fix`: its fallback comment above the original text,
    in the file's comment `style` (`//Text` in C#, like the approved PRs).

    None when the fix has no fallback note.
    """
    note = (fix.fallback_note or "").strip()
    if not note or not fix.find.strip():
        return None
    first_line = fix.find.replace("\r\n", "\n").split("\n")[0]
    indent = first_line[: len(first_line) - len(first_line.lstrip())]
    comment = note if note.startswith(style.line) else f"{style.line}{note}{style.end}"
    return PlannedEdit(
        file=fix.file,
        find=fix.find,
        replace=f"{indent}{comment}\n{fix.find}",
        kind=EditKind.NOTE,
        finding_ids=fix.finding_ids,
        reason=f"Fallback for a dropped fix: {fix.reason}",
        fallback_note=None,
        note_section=NoteSection.FOR_YOU,
    )


def _apply(
    edit: PlannedEdit, files: Mapping[str, bytes], texts: dict[str, str], rules: EditRules
) -> str | None:
    if _placeholder(edit.reason):
        return "the reason is empty or a placeholder; give one real sentence saying why"
    path = edit.file.replace("\\", "/")
    problem = _path_problem(path, files, edit.kind, rules) or _notes_only_problem(path, edit, rules)
    if problem:
        return problem
    if path not in texts:
        try:
            texts[path] = files[path].decode("utf-8")
        except UnicodeDecodeError:
            return f"{path} is not UTF-8 text"
    text = texts[path]
    if edit.kind is EditKind.FINAL_NEWLINE:
        return _final_newline(path, text, texts)
    return _replace(path, text, edit, rules, texts)


def _placeholder(reason: str) -> bool:
    text = reason.strip()
    return len(text) < MIN_REASON_CHARS or text.lower().rstrip(".") in PLACEHOLDER_REASONS


def _path_problem(
    path: str, files: Mapping[str, bytes], kind: EditKind, rules: EditRules
) -> str | None:
    if path.startswith("/") or _DRIVE_LETTER.match(path) or ".." in path.split("/"):
        return f"{path} is not a path inside the repo"
    if path not in files:
        return f"{path} does not exist; edits never add, delete or rename files"
    if kind is EditKind.FINAL_NEWLINE:
        return None if path in rules.data_files else f"{path} is not the data file"
    if rules.style(path) is None:
        suffixes = " or ".join(rules.styles)
        return f"{path} is not a {suffixes} file; only {suffixes} files are edited"
    if rules.editable is not None and path not in rules.editable:
        return f"{path} was not shown to you; only the files shown are edited"
    return None


def _notes_only_problem(path: str, edit: PlannedEdit, rules: EditRules) -> str | None:
    """A `fix` for a notes-only requirement, or a `fix` that claims nothing in a file only
    notes-only findings point to."""
    if edit.kind is not EditKind.FIX:
        return None
    claimed = [item for item in edit.finding_ids if item in rules.notes_only]
    if claimed:
        verb = "allows" if len(claimed) == 1 else "allow"
        return f"{', '.join(claimed)} {verb} notes only; plan a note, not a fix"
    if not edit.finding_ids and path in rules.notes_only_files:
        return (
            f"only notes-only findings point to {path}, so it gets notes only; plan a note, "
            "not a fix"
        )
    return None


def _final_newline(path: str, text: str, texts: dict[str, str]) -> str | None:
    if not text or text.endswith("\n"):
        return f"{path} already ends with a newline"
    texts[path] = text + ("\r\n" if "\r\n" in text else "\n")
    return None


def _replace(
    path: str, text: str, edit: PlannedEdit, rules: EditRules, texts: dict[str, str]
) -> str | None:
    find = edit.find.replace("\r\n", "\n")
    replace = edit.replace.replace("\r\n", "\n")
    if not find.strip():
        return "find is empty"
    if find == replace:
        return "the edit changes nothing"
    crlf = "\r\n" in text
    if crlf and re.search(r"(?<!\r)\n", text):
        return f"{path} mixes line endings"
    newline = "\r\n" if crlf else "\n"
    count = text.count(find.replace("\n", newline))
    if count != 1:
        return f"find occurs {count} times in {path}; it must occur exactly once"
    style = rules.style(path)
    if style is None:  # Refused by the path check.
        return f"{path} is not edited"
    problem = _content_problem(path, find, replace, edit.kind, rules, style)
    if problem is None and rules.allowances is not None:
        problem = detector_problem(edit, rules.allowances)
    if problem:
        return problem
    texts[path] = text.replace(find.replace("\n", newline), replace.replace("\n", newline), 1)
    return None


def _content_problem(
    path: str, find: str, replace: str, kind: EditKind, rules: EditRules, style: CommentStyle
) -> str | None:
    old, new = find.split("\n"), replace.split("\n")
    opcodes = difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    added = [line for tag, _, _, j1, j2 in opcodes if tag != "equal" for line in new[j1:j2]]
    removed = [line for tag, i1, i2, _, _ in opcodes if tag != "equal" for line in old[i1:i2]]
    for forbidden in rules.forbidden:
        if forbidden.covers(path) and _adds(forbidden.item, find, replace, added):
            return forbidden.item.message
    if kind is EditKind.NOTE:
        return _note_problem(opcodes, added, style)
    if style.end and any(
        line.strip().startswith(style.line) and not _is_comment(line, style) for line in added
    ):
        return f"an added {style.line} comment must end with {style.end} on the same line"
    for change in rules.forbidden_changes:
        if change.covers(path) and any(change.item.regex.search(line) for line in removed):
            return change.item.message
    return _fix_problem(opcodes, new, added, removed, rules, style)


def _adds(forbidden: ForbiddenText, find: str, replace: str, added: list[str]) -> bool:
    regex = forbidden.regex
    if forbidden.new_only:
        return len(regex.findall(replace)) > len(regex.findall(find))
    return any(regex.search(line) for line in added)


def _note_problem(
    opcodes: Sequence[tuple[str, int, int, int, int]], added: list[str], style: CommentStyle
) -> str | None:
    only_comments = (
        f"a note may only add {style.line} comment lines and must leave the code unchanged"
    )
    if any(tag not in {"equal", "insert"} for tag, *_ in opcodes):
        return only_comments
    if not all(_is_comment(line, style) for line in added if line.strip()):
        return only_comments
    if any(style.marker_pattern.match(line.strip()) for line in added):
        return (
            f"a note must not carry a {style.fix_marker} line; {style.fix_marker} marks a "
            "code change"
        )
    return None


def _fix_problem(
    opcodes: Sequence[tuple[str, int, int, int, int]],
    new: list[str],
    added: list[str],
    removed: list[str],
    rules: EditRules,
    style: CommentStyle,
) -> str | None:
    changed = [line for line in removed if line.strip()]
    changed += [line for line in added if line.strip() and not _is_comment(line, style)]
    if not any(not _is_comment(line, style) for line in changed):
        return "a fix must change code; a comment-only edit is a note"
    if len(changed) > rules.max_fix_lines:
        return f"a fix changes {len(changed)} code lines; at most {rules.max_fix_lines}"
    for tag, _, _, j1, j2 in opcodes:
        if tag != "equal" and not _marked(new[j1:j2], style):
            return f"a fix needs a {style.fix_marker} ... line directly above the changed code"
    return None


def _marked(segment: list[str], style: CommentStyle) -> bool:
    """Whether the changed lines start with a fix marker line before any code line."""
    for line in segment:
        stripped = line.strip()
        if style.marker_pattern.match(stripped):
            return True
        if stripped and not _is_comment(line, style):
            return False
    return False


def _is_comment(line: str, style: CommentStyle) -> bool:
    stripped = line.strip()
    if not stripped.startswith(style.line):
        return False
    return not style.end or (
        stripped.endswith(style.end)
        and stripped.find(style.end, len(style.line)) == len(stripped) - len(style.end)
    )
