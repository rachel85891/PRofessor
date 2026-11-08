"""Which self-review violations are acted on, and the detectors of safe fixes
(docs/fix-pr-workflow.md §3).

The self-review (role: judge) reports edits that break a review rule, citing the rule's
number and quoting its words. A violation is acted on only when the quote is from that
rule and the edit is not one the rules allow outright: a safe fix whose detector a
`fix_allowed` rule names, or a note in a rule's approved wording (ADR 0023). On the pilots
the self-review removed exactly the edits the approved PRs made (stage 14c), so these
checks are in code, not only in the prompt; which edits they allow is the rules' data.

A detector recognizes one shape of fix. A fix of that shape that is still not allowed (a
commented-out `using`, a reference no finding names) is a problem the guardrails reject
before the fix is applied (`detector_problem`).
"""

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from hw_checker.domain import (
    ApprovedNote,
    CommentStyle,
    EditKind,
    PlannedEdit,
    SafeFix,
    SelfReview,
)
from hw_checker.services.review_policy import DetectorGrant, Scoped

MIN_QUOTE_CHARS = 10
"""A shorter quote cannot show which part of a rule the edit breaks."""
_RULE_LINE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
_ID_ASSIGNMENT = re.compile(r"^\w+(?:\.\w+)*\.[Ii]d\s*=\s*\w*[Ii]d\s*;$")
_STRAY_USING = re.compile(r"^using\s+[\w.]+\s*;$")
_CONTROLLER_CLASS = re.compile(r"\bclass\s+(\w+)Controller\b")
_READONLY = "readonly "
_ATTRIBUTE_REFERENCE = re.compile(
    r"""\b(?:src|href|id)\s*=\s*(?P<quote>["'])(?P<value>[^"'<>]*)(?P=quote)""", re.IGNORECASE
)
_LOOKUP_REFERENCE = re.compile(
    r"""\b(?:getElementById|querySelector(?:All)?)\s*\(\s*(?P<quote>["'`])#?"""
    r"""(?P<value>[^"'`]*)(?P=quote)"""
)
_IF = re.compile(r"\bif\s*\(")
_LONE_EQUALS = re.compile(r"(?<![=!<>+\-*/%&|^?:])=(?![=>])")
_STRICT_EQUALS = "==="


@dataclass(frozen=True)
class Allowances:
    """What the rules in effect allow outright: the safe fixes their `fix_allowed` rules
    name, and their notes in the approved wording, each in the file types its rule is
    limited to; `styles` tell comments from code, and `findings` (requirement id → the
    findings' messages) say what the checks named."""

    detectors: tuple[Scoped[DetectorGrant], ...]
    approved_notes: tuple[Scoped[ApprovedNote], ...]
    styles: Mapping[str, CommentStyle]
    findings: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def style(self, path: str) -> CommentStyle | None:
        """The comment style of the file at `path`."""
        return next((style for suffix, style in self.styles.items() if path.endswith(suffix)), None)

    def detectors_for(self, path: str) -> list[DetectorGrant]:
        """The safe fixes allowed in the file at `path`."""
        return [scoped.item for scoped in self.detectors if scoped.covers(path)]

    def notes_for(self, path: str) -> list[ApprovedNote]:
        """The notes in the approved wording for the file at `path`."""
        return [scoped.item for scoped in self.approved_notes if scoped.covers(path)]

    def named(self, grant: DetectorGrant) -> tuple[str, ...]:
        """The messages of the findings of the requirements `grant` is `named_by`."""
        return tuple(text for item in grant.named_by for text in self.findings.get(item, ()))


@dataclass(frozen=True)
class ReviewOutcome:
    """The violations acted on (edit index → reason) and the ignored ones' reasons."""

    broken: dict[int, str]
    ignored: tuple[str, ...]


@dataclass(frozen=True)
class Change:
    """A fix as a detector sees it: its stripped code lines added and removed, and the
    comment lines it adds, in the file's comment `style`."""

    added: list[str]
    removed: list[str]
    comments: list[str]
    style: CommentStyle
    named: tuple[str, ...] = ()
    """The messages of the findings that may name what the fix changes."""


@dataclass(frozen=True)
class Detected:
    """A fix of a detector's shape; `problem` says why it is still not allowed."""

    problem: str | None = None


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
    allowed = [grant.detector for grant in allowances.detectors_for(edit.file)]
    if edit.kind is EditKind.FINAL_NEWLINE:
        return _SAFE_FIXES[SafeFix.FINAL_NEWLINE] if SafeFix.FINAL_NEWLINE in allowed else None
    style = allowances.style(edit.file)
    if style is None:
        return None
    if edit.kind is EditKind.NOTE:
        text = " ".join(_change(edit, style).comments).lower()
        return next(
            (note.what for note in allowances.notes_for(edit.file) if note.words.lower() in text),
            None,
        )
    return next(
        (
            _SAFE_FIXES[detector]
            for detector, detected in _detections(edit, allowances, style)
            if detected.problem is None
        ),
        None,
    )


def detector_problem(edit: PlannedEdit, allowances: Allowances) -> str | None:
    """Why a `fix` of a safe fix's shape is still not allowed (the first detector in effect
    that says so); None when no detector objects."""
    style = allowances.style(edit.file)
    if edit.kind is not EditKind.FIX or style is None:
        return None
    return next(
        (
            detected.problem
            for _, detected in _detections(edit, allowances, style)
            if detected.problem is not None
        ),
        None,
    )


def _detections(
    edit: PlannedEdit, allowances: Allowances, style: CommentStyle
) -> list[tuple[SafeFix, Detected]]:
    """What each detector in effect for the edit's file makes of it, in detector order."""
    grants = allowances.detectors_for(edit.file)
    found: list[tuple[SafeFix, Detected]] = []
    for detector, detect in _DETECTORS.items():
        for grant in (grant for grant in grants if grant.detector is detector):
            detected = detect(_change(edit, style, allowances.named(grant)))
            if detected is not None:
                found.append((detector, detected))
    return found


def _change(edit: PlannedEdit, style: CommentStyle, named: tuple[str, ...] = ()) -> Change:
    old = edit.find.replace("\r\n", "\n").split("\n")
    new = edit.replace.replace("\r\n", "\n").split("\n")
    opcodes = difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    added = [line.strip() for tag, _, _, j1, j2 in opcodes if tag != "equal" for line in new[j1:j2]]
    removed = [
        line.strip() for tag, i1, i2, _, _ in opcodes if tag != "equal" for line in old[i1:i2]
    ]
    return Change(
        added=[line for line in added if line and not line.startswith(style.line)],
        removed=[line for line in removed if line],
        comments=[line for line in added if line.startswith(style.line)],
        style=style,
        named=named,
    )


def _missing_break(change: Change) -> Detected | None:
    # A break after a loop's match may need the `if` body wrapped in braces.
    added = [line for line in change.added if line not in ("{", "}")]
    return Detected() if not change.removed and added == ["break;"] else None


def _id_assignment(change: Change) -> Detected | None:
    added, removed = change.added, change.removed
    matches = not removed and len(added) == 1 and bool(_ID_ASSIGNMENT.match(added[0]))
    return Detected() if matches else None


def _stray_using(change: Change) -> Detected | None:
    """`using` lines deleted, with the fix marker line where they were. A `using` commented
    out instead of deleted is refused (the approved PRs removed it, or asked)."""
    removed = change.removed
    if change.added or not removed or not all(_STRAY_USING.match(line) for line in removed):
        return None
    style = change.style
    kept = {
        comment.removeprefix(style.line).removesuffix(style.end).strip()
        for comment in change.comments
    }
    if any(line in kept for line in removed):
        return Detected(
            f"a stray `using` is deleted, with its {style.fix_marker} line where it was; "
            "never comment it out"
        )
    return Detected()


def _readonly_field(change: Change) -> Detected | None:
    if len(change.added) != 1 or len(change.removed) != 1:
        return None
    before, after = change.removed[0], change.added[0]
    matches = _READONLY in after and after.replace(_READONLY, "", 1) == before
    return Detected() if matches else None


def _controller_suffix(change: Change) -> Detected | None:
    if len(change.added) != 1 or len(change.removed) != 1:
        return None
    before, after = change.removed[0], change.added[0]
    match = _CONTROLLER_CLASS.search(after)
    matches = match is not None and after.replace(f"{match[1]}Controller", match[1], 1) == before
    return Detected() if matches else None


def _client_reference(change: Change) -> Detected | None:
    """One line where only one reference changes: a `src`, `href` or `id` attribute, or the
    id a `getElementById` / `querySelector` looks up. It is allowed only when a finding
    names the old or the new value."""
    if len(change.added) != 1 or len(change.removed) != 1:
        return None
    before, old = _references(change.removed[0])
    after, new = _references(change.added[0])
    if before != after or len(old) != len(new):
        return None
    changed = [(was, now) for was, now in zip(old, new, strict=True) if was != now]
    if len(changed) != 1:
        return None
    was, now = changed[0]
    if any(_names(text, value) for text in change.named for value in (was, now) if value):
        return Detected()
    return Detected(
        f"the reference `{was}` is changed to `{now}`, but no check's finding names it; "
        "change a reference only when a finding names it, or ask in a note instead"
    )


def _references(line: str) -> tuple[str, list[str]]:
    """The line with each reference's value cut out, and the values in order."""
    found = sorted(
        (match.span("value"), match["value"])
        for pattern in (_ATTRIBUTE_REFERENCE, _LOOKUP_REFERENCE)
        for match in pattern.finditer(line)
    )
    parts: list[str] = []
    last = 0
    for (start, end), _ in found:
        parts.append(line[last:start])
        last = end
    parts.append(line[last:])
    return "\0".join(parts), [value for _, value in found]


def _names(text: str, value: str) -> bool:
    return re.search(rf"(?<![\w./-]){re.escape(value)}(?![\w/-])", text) is not None


def _assignment_in_condition(change: Change) -> Detected | None:
    """`=` changed to `===` inside an `if (…)` condition, and nothing else on the line."""
    if len(change.added) != 1 or len(change.removed) != 1:
        return None
    before, after = change.removed[0], change.added[0]
    span = _condition(before)
    if span is None:
        return None
    lone = [match.start() for match in _LONE_EQUALS.finditer(before, *span)]
    if not lone:
        return None
    if any(f"{before[:index]}{_STRICT_EQUALS}{before[index + 1 :]}" == after for index in lone):
        return Detected()
    if after.count(_STRICT_EQUALS) > before.count(_STRICT_EQUALS):
        return Detected(
            f"an `=` in an `if` condition is fixed only by changing it to `{_STRICT_EQUALS}`; "
            "nothing else on the line changes"
        )
    return None


def _condition(line: str) -> tuple[int, int] | None:
    """Where the first `if (…)` condition of the line is (inside its parentheses)."""
    match = _IF.search(line)
    if match is None:
        return None
    depth = 0
    for index in range(match.end() - 1, len(line)):
        depth += {"(": 1, ")": -1}.get(line[index], 0)
        if depth == 0:
            return match.end(), index
    return None


_DETECTORS: dict[SafeFix, Callable[[Change], Detected | None]] = {
    SafeFix.MISSING_BREAK: _missing_break,
    SafeFix.ID_ASSIGNMENT: _id_assignment,
    SafeFix.STRAY_USING: _stray_using,
    SafeFix.READONLY_FIELD: _readonly_field,
    SafeFix.CONTROLLER_SUFFIX: _controller_suffix,
    SafeFix.CLIENT_REFERENCE: _client_reference,
    SafeFix.ASSIGNMENT_IN_CONDITION: _assignment_in_condition,
}
"""Each code detector, in the order they are tried (a data file's newline is its own kind)."""
_SAFE_FIXES = {
    SafeFix.MISSING_BREAK: "a missing break",
    SafeFix.ID_ASSIGNMENT: "an id lost on update, restored with one assignment",
    SafeFix.STRAY_USING: "a removed stray using",
    SafeFix.READONLY_FIELD: "readonly for a fixed path",
    SafeFix.CONTROLLER_SUFFIX: "the Controller suffix on a controller class",
    SafeFix.FINAL_NEWLINE: "a data file's final newline",
    SafeFix.CLIENT_REFERENCE: "a file or element reference a check's finding names",
    SafeFix.ASSIGNMENT_IN_CONDITION: "= changed to === in an if condition",
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
