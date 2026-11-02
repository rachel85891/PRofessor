"""Deterministic guardrails for the fixer's edits (ADR 0021, docs/fix-pr-workflow.md §3).

The model only plans edits; these rules decide which are applied. Each edit is checked on
its own, in plan order, against the files as the earlier edits left them, and a rejected
edit keeps its reason for the next attempt. An applied edit replaces exactly one
occurrence of its text, so nothing outside the edited lines changes; every file keeps its
byte-order mark and line endings.
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from hw_checker.domain import EditApplication, EditKind, NoteSection, PlannedEdit, RejectedEdit

CODE_SUFFIX = ".cs"
FORBIDDEN_TEXT = "File.Exists"
BAD_REQUEST = "BadRequest("
_HEBREW = re.compile(r"[\u0590-\u05FF]")
_LECTURER = re.compile(r"\blecturer", re.IGNORECASE)
_FIX_MARKER = re.compile(r"^//\s*fix:", re.IGNORECASE)
_PROPERTY_DECLARATION = re.compile(r"\{\s*get;\s*(?:set|init);\s*\}")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class EditRules:
    """The configurable limits of the guardrails."""

    max_edits: int
    """At most this many edits are applied per repo; later ones are rejected."""
    max_fix_lines: int
    """A `fix` changes at most this many code lines."""
    data_files: frozenset[str] = field(default_factory=frozenset)
    """The repo's data files, the only ones a `final_newline` edit may change."""


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


def fallback_edit(fix: PlannedEdit) -> PlannedEdit | None:
    """The note that replaces a dropped `fix`: its fallback comment above the original text,
    written `//Text` like the approved PRs.

    None when the fix has no fallback note.
    """
    note = (fix.fallback_note or "").strip()
    if not note or not fix.find.strip():
        return None
    first_line = fix.find.replace("\r\n", "\n").split("\n")[0]
    indent = first_line[: len(first_line) - len(first_line.lstrip())]
    comment = note if note.startswith("//") else f"//{note}"
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
    path = edit.file.replace("\\", "/")
    problem = _path_problem(path, files, edit.kind, rules)
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


def _path_problem(
    path: str, files: Mapping[str, bytes], kind: EditKind, rules: EditRules
) -> str | None:
    if path.startswith("/") or _DRIVE_LETTER.match(path) or ".." in path.split("/"):
        return f"{path} is not a path inside the repo"
    if path not in files:
        return f"{path} does not exist; edits never add, delete or rename files"
    if kind is EditKind.FINAL_NEWLINE:
        return None if path in rules.data_files else f"{path} is not the data file"
    if not path.endswith(CODE_SUFFIX):
        return f"{path} is not a .cs file; only .cs files are edited"
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
    problem = _content_problem(find, replace, edit.kind, rules)
    if problem:
        return problem
    texts[path] = text.replace(find.replace("\n", newline), replace.replace("\n", newline), 1)
    return None


def _content_problem(find: str, replace: str, kind: EditKind, rules: EditRules) -> str | None:
    old, new = find.split("\n"), replace.split("\n")
    opcodes = difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    added = [line for tag, _, _, j1, j2 in opcodes if tag != "equal" for line in new[j1:j2]]
    removed = [line for tag, i1, i2, _, _ in opcodes if tag != "equal" for line in old[i1:i2]]
    if any(FORBIDDEN_TEXT in line for line in added):
        return "added text must never use File.Exists"
    if replace.count(BAD_REQUEST) > find.count(BAD_REQUEST):
        return "added text must never return a new BadRequest("
    if any(_HEBREW.search(line) for line in added):
        return "added text must be English, never Hebrew"
    if any(_LECTURER.search(line) for line in added):
        return 'added text says "The assignment asks...", never mentions the lecturer'
    if kind is EditKind.NOTE:
        return _note_problem(opcodes, added)
    return _fix_problem(opcodes, new, added, removed, rules)


def _note_problem(
    opcodes: Sequence[tuple[str, int, int, int, int]], added: list[str]
) -> str | None:
    if any(tag not in {"equal", "insert"} for tag, *_ in opcodes):
        return "a note may only add // comment lines and must leave the code unchanged"
    if not all(_is_comment(line) for line in added if line.strip()):
        return "a note may only add // comment lines and must leave the code unchanged"
    if any(_FIX_MARKER.match(line.strip()) for line in added):
        return "a note must not carry a //fix: line; //fix: marks a code change"
    return None


def _fix_problem(
    opcodes: Sequence[tuple[str, int, int, int, int]],
    new: list[str],
    added: list[str],
    removed: list[str],
    rules: EditRules,
) -> str | None:
    if any(_PROPERTY_DECLARATION.search(line) for line in removed):
        return "a fix never changes a model's property declaration ({ get; set; })"
    changed = [line for line in removed if line.strip()]
    changed += [line for line in added if line.strip() and not _is_comment(line)]
    if not any(not _is_comment(line) for line in changed):
        return "a fix must change code; a comment-only edit is a note"
    if len(changed) > rules.max_fix_lines:
        return f"a fix changes {len(changed)} code lines; at most {rules.max_fix_lines}"
    for tag, _, _, j1, j2 in opcodes:
        if tag != "equal" and not _marked(new[j1:j2]):
            return "a fix needs a //fix: ... line directly above the changed code"
    return None


def _marked(segment: list[str]) -> bool:
    """Whether the changed lines start with a //fix: line before any code line."""
    for line in segment:
        stripped = line.strip()
        if _FIX_MARKER.match(stripped):
            return True
        if stripped and not _is_comment(line):
            return False
    return False


def _is_comment(line: str) -> bool:
    return line.strip().startswith("//")
