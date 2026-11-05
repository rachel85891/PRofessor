"""Which self-review violations are acted on (docs/fix-pr-workflow.md §3).

The self-review (role: judge) reports edits that break a review rule, citing the rule's
number and quoting its words. A violation is acted on only when the quote is from that
rule and the edit is not one the rules allow outright: a safe fix whose detector a
`fix_allowed` rule names, or a note in a rule's approved wording (ADR 0023). On the pilots
the self-review removed exactly the edits the approved PRs made (stage 14c), so these
checks are in code, not only in the prompt; which edits they allow is the rules' data.
"""

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from hw_checker.domain import (
    ApprovedNote,
    CommentStyle,
    EditKind,
    PlannedEdit,
    SafeFix,
    SelfReview,
)

MIN_QUOTE_CHARS = 10
"""A shorter quote cannot show which part of a rule the edit breaks."""
_RULE_LINE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
_ID_ASSIGNMENT = re.compile(r"^\w+(?:\.\w+)*\.[Ii]d\s*=\s*\w*[Ii]d\s*;$")
_STRAY_USING = re.compile(r"^using\s+[\w.]+\s*;$")
_CONTROLLER_CLASS = re.compile(r"\bclass\s+(\w+)Controller\b")
_READONLY = "readonly "


@dataclass(frozen=True)
class Allowances:
    """What the rules in effect allow outright: the safe fixes their `fix_allowed` rules
    name, and their notes in the approved wording; `styles` tell comments from code."""

    detectors: frozenset[SafeFix]
    approved_notes: tuple[ApprovedNote, ...]
    styles: Mapping[str, CommentStyle]

    def style(self, path: str) -> CommentStyle | None:
        """The comment style of the file at `path`."""
        return next((style for suffix, style in self.styles.items() if path.endswith(suffix)), None)


@dataclass(frozen=True)
class ReviewOutcome:
    """The violations acted on (edit index → reason) and the ignored ones' reasons."""

    broken: dict[int, str]
    ignored: tuple[str, ...]


def numbered_rules(text: str) -> dict[int, str]:
    """The rendered review rules by number, from lines like `3. Never implement ...`."""
    return {int(number): rule for number, rule in _RULE_LINE.findall(text)}


def judge(
    review: SelfReview,
    applied: Sequence[PlannedEdit],
    rules: Mapping[int, str],
    allowances: Allowances,
) -> ReviewOutcome:
    """Decide which of `review`'s violations to act on; the others are ignored with a reason.

    The first acted-on violation of an edit gives its reason; a violation of an edit index
    that does not exist is dropped.
    """
    broken: dict[int, str] = {}
    ignored: list[str] = []
    for violation in review.violations:
        if not 0 <= violation.edit_index < len(applied):
            continue
        edit = applied[violation.edit_index]
        cited = f"rule {violation.rule}: {violation.explanation}"
        problem = _quote_problem(violation.rule, violation.quote, rules)
        allowed = never_a_violation(edit, allowances)
        if problem is None and allowed is not None:
            problem = f"{allowed} is never a violation"
        if problem is not None:
            ignored.append(
                f"Self-review of edit {violation.edit_index} ({edit.kind.value} in {edit.file}) "
                f"ignored, {problem} ({cited})"
            )
            continue
        broken.setdefault(violation.edit_index, f"self-review: {cited}")
    return ReviewOutcome(broken=broken, ignored=tuple(ignored))


def never_a_violation(edit: PlannedEdit, allowances: Allowances) -> str | None:
    """What the edit is, when the rules allow it outright; None otherwise."""
    if edit.kind is EditKind.FINAL_NEWLINE:
        return (
            _SAFE_FIXES[SafeFix.FINAL_NEWLINE]
            if SafeFix.FINAL_NEWLINE in allowances.detectors
            else None
        )
    style = allowances.style(edit.file)
    if style is None:
        return None
    old = edit.find.replace("\r\n", "\n").split("\n")
    new = edit.replace.replace("\r\n", "\n").split("\n")
    opcodes = difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    added = [line.strip() for tag, _, _, j1, j2 in opcodes if tag != "equal" for line in new[j1:j2]]
    removed = [
        line.strip() for tag, i1, i2, _, _ in opcodes if tag != "equal" for line in old[i1:i2]
    ]
    comments = [line for line in added if line.startswith(style.line)]
    added_code = [line for line in added if line and not line.startswith(style.line)]
    removed_code = [line for line in removed if line]
    if edit.kind is EditKind.NOTE:
        text = " ".join(comments).lower()
        return next(
            (note.what for note in allowances.approved_notes if note.words.lower() in text), None
        )
    return next(
        (
            _SAFE_FIXES[detector]
            for detector, detects in _DETECTORS.items()
            if detector in allowances.detectors and detects(added_code, removed_code)
        ),
        None,
    )


def _missing_break(added: list[str], removed: list[str]) -> bool:
    # A break after a loop's match may need the `if` body wrapped in braces.
    return not removed and [line for line in added if line not in ("{", "}")] == ["break;"]


def _id_assignment(added: list[str], removed: list[str]) -> bool:
    return not removed and len(added) == 1 and bool(_ID_ASSIGNMENT.match(added[0]))


def _stray_using(added: list[str], removed: list[str]) -> bool:
    return not added and bool(removed) and all(_STRAY_USING.match(line) for line in removed)


def _readonly_field(added: list[str], removed: list[str]) -> bool:
    if len(added) != 1 or len(removed) != 1:
        return False
    before, after = removed[0], added[0]
    return _READONLY in after and after.replace(_READONLY, "", 1) == before


def _controller_suffix(added: list[str], removed: list[str]) -> bool:
    if len(added) != 1 or len(removed) != 1:
        return False
    before, after = removed[0], added[0]
    match = _CONTROLLER_CLASS.search(after)
    return match is not None and after.replace(f"{match[1]}Controller", match[1], 1) == before


_DETECTORS: dict[SafeFix, Callable[[list[str], list[str]], bool]] = {
    SafeFix.MISSING_BREAK: _missing_break,
    SafeFix.ID_ASSIGNMENT: _id_assignment,
    SafeFix.STRAY_USING: _stray_using,
    SafeFix.READONLY_FIELD: _readonly_field,
    SafeFix.CONTROLLER_SUFFIX: _controller_suffix,
}
"""Each code detector, in the order they are tried (a data file's newline is its own kind)."""
_SAFE_FIXES = {
    SafeFix.MISSING_BREAK: "a missing break",
    SafeFix.ID_ASSIGNMENT: "an id lost on update, restored with one assignment",
    SafeFix.STRAY_USING: "a removed stray using",
    SafeFix.READONLY_FIELD: "readonly for a fixed path",
    SafeFix.CONTROLLER_SUFFIX: "the Controller suffix on a controller class",
    SafeFix.FINAL_NEWLINE: "a data file's final newline",
}
"""What each detector recognizes, for the repo report."""


def _quote_problem(number: int, quote: str, rules: Mapping[int, str]) -> str | None:
    rule = rules.get(number)
    if rule is None:
        return f"there is no rule {number}"
    words = _normalized(quote)
    if len(words) < MIN_QUOTE_CHARS or words not in _normalized(rule):
        return f"its quote is not from rule {number}"
    return None


def _normalized(text: str) -> str:
    return " ".join(text.replace("`", "").replace("…", "...").lower().split())
