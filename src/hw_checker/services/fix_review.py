"""Which self-review violations are acted on (docs/fix-pr-workflow.md §3).

The self-review (role: judge) reports edits that break a review rule, citing the rule's
number and quoting its words. A violation is acted on only when the quote is from that
rule and the edit is not one the rules allow outright: the small safe fixes of rule 2 and
the notes in the approved wording. On the pilots the self-review removed exactly the edits
the approved PRs made (stage 14c), so these checks are in code, not only in the prompt.
"""

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from hw_checker.domain import EditKind, PlannedEdit, SelfReview

MIN_QUOTE_CHARS = 10
"""A shorter quote cannot show which part of a rule the edit breaks."""
APPROVED_NOTES = {
    "why isn't it implemented": "the GET-by-id question in the approved wording",
    "the assignment asks put to return ok()": "the PUT note in the approved wording",
    "propertynamecaseinsensitive": "a naming note with the PropertyNameCaseInsensitive tip",
}
"""Words of the approved notes (lowercased) and what they are."""
_RULE_LINE = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
_ID_ASSIGNMENT = re.compile(r"^\w+(?:\.\w+)*\.[Ii]d\s*=\s*\w*[Ii]d\s*;$")
_STRAY_USING = re.compile(r"^using\s+[\w.]+\s*;$")
_CONTROLLER_CLASS = re.compile(r"\bclass\s+(\w+)Controller\b")
_READONLY = "readonly "


@dataclass(frozen=True)
class ReviewOutcome:
    """The violations acted on (edit index → reason) and the ignored ones' reasons."""

    broken: dict[int, str]
    ignored: tuple[str, ...]


def numbered_rules(text: str) -> dict[int, str]:
    """The rendered review rules by number, from lines like `3. Never implement ...`."""
    return {int(number): rule for number, rule in _RULE_LINE.findall(text)}


def judge(
    review: SelfReview, applied: Sequence[PlannedEdit], rules: Mapping[int, str]
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
        allowed = never_a_violation(edit)
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


def never_a_violation(edit: PlannedEdit) -> str | None:
    """What the edit is, when the rules allow it outright; None otherwise."""
    if edit.kind is EditKind.FINAL_NEWLINE:
        return "a data file's final newline"
    old = edit.find.replace("\r\n", "\n").split("\n")
    new = edit.replace.replace("\r\n", "\n").split("\n")
    opcodes = difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    added = [line.strip() for tag, _, _, j1, j2 in opcodes if tag != "equal" for line in new[j1:j2]]
    removed = [
        line.strip() for tag, i1, i2, _, _ in opcodes if tag != "equal" for line in old[i1:i2]
    ]
    comments = [line for line in added if line.startswith("//")]
    added_code = [line for line in added if line and not line.startswith("//")]
    removed_code = [line for line in removed if line]
    if edit.kind is EditKind.NOTE:
        text = " ".join(comments).lower()
        return next((what for words, what in APPROVED_NOTES.items() if words in text), None)
    return _safe_fix(added_code, removed_code)


def _safe_fix(added: list[str], removed: list[str]) -> str | None:
    if not removed and added == ["break;"]:
        return "a missing break"
    if not removed and len(added) == 1 and _ID_ASSIGNMENT.match(added[0]):
        return "an id lost on update, restored with one assignment"
    if not added and removed and all(_STRAY_USING.match(line) for line in removed):
        return "a removed stray using"
    if len(added) == 1 and len(removed) == 1:
        before, after = removed[0], added[0]
        if _READONLY in after and after.replace(_READONLY, "", 1) == before:
            return "readonly for a fixed path"
        match = _CONTROLLER_CLASS.search(after)
        if match and after.replace(f"{match[1]}Controller", match[1], 1) == before:
            return "the Controller suffix on a controller class"
    return None


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
