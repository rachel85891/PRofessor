"""The review policy one role sees for one assignment (ADR 0023, design §8.1 and §8.2).

The course's rules and examples come first, then each assignment's up to the checked one,
by `order`; `replaces`, `after` and `until` shape the list, and each role keeps only the
items that apply to it and to the file types its prompt shows. The rules are numbered from
1 in that order, and their texts' placeholders are filled here, so a prompt's frame only
shows them; a rule naming the failing notes-only requirements is left out when none fails.
A guardrail or allowance keeps the file types its rule is limited to.
"""

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from hw_checker.domain import (
    ApprovedNote,
    Assignment,
    Course,
    Feedback,
    FixExample,
    ForbiddenText,
    NoteSection,
    ReviewRole,
    ReviewRule,
    SafeFix,
)
from hw_checker.domain.course import (
    NOTES_ONLY,
    RULE_REFERENCE,
    SHORT_NAME,
    TAUGHT_SO_FAR,
    PolicyItem,
    RulePlace,
    covers,
    in_effect,
)

TECHNIQUES = "{techniques}"
"""In the course's `taught_so_far`: the taught techniques, comma-separated."""


@dataclass(frozen=True)
class Scoped[ItemT]:
    """A rule's guardrail or allowance, and the file types its rule is limited to."""

    item: ItemT
    files: tuple[str, ...] = ()
    """The file types (suffixes); empty: every file."""

    def covers(self, path: str) -> bool:
        """Whether it applies to an edit of the file at `path`."""
        return covers(self.files, path)


@dataclass(frozen=True)
class DetectorGrant:
    """A safe fix a rule allows, and the requirements whose findings must name what it
    changes (its rule's `named_by`)."""

    detector: SafeFix
    named_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShownExample:
    """An example as a prompt shows it: an invented situation, or the number of the rule
    whose own example it is, and the plan's JSON."""

    situation: str
    rule: int | None
    plan: str


@dataclass(frozen=True)
class RolePolicy:
    """What one role's prompt and the mechanism read from the rules in effect."""

    rules: tuple[str, ...]
    """The rules' texts, numbered from 1 in this order."""
    allowed: tuple[str, ...]
    """For the judge: what is never a violation, one line each."""
    clarifications: tuple[str, ...]
    examples: tuple[ShownExample, ...]
    detectors: tuple[Scoped[DetectorGrant], ...]
    approved_notes: tuple[Scoped[ApprovedNote], ...]
    forbidden: tuple[Scoped[ForbiddenText], ...]
    """Text an edit must never add."""
    forbidden_changes: tuple[Scoped[ForbiddenText], ...] = ()
    """Text a fix must never remove or change."""
    fix_conditions: tuple[str, ...] = ()
    """For the fixer: the rules shown with the `fix` edit kind, not numbered."""
    note_sections: tuple[Scoped[NoteSection], ...] = ()
    """The PR sections of note-only rules and the file types they apply to."""

    def slots(self) -> dict[str, object]:
        """The values a prompt's frame shows."""
        return {
            "rules": list(self.rules),
            "allowed": list(self.allowed),
            "clarifications": list(self.clarifications),
            "examples": list(self.examples),
            "fix_conditions": list(self.fix_conditions),
        }


def role_policy(
    course: Course,
    assignment: Assignment,
    assignments: Sequence[Assignment],
    role: ReviewRole,
    files: Collection[str] = (),
    found: Collection[str] = (),
) -> RolePolicy:
    """The policy `role` sees when `assignment` is checked; `assignments` are the round's
    (`_general` and 1..N), `files` the paths the prompt shows (the edited ones, for the
    writer) and `found` the requirements with a finding. An item limited to file types is
    left out when no shown file is of them, and one `when_found` when none of its
    requirements has a finding."""
    earlier = sorted((a for a in assignments if a.order <= assignment.order), key=lambda a: a.order)
    passed = {a.id: a.order < assignment.order for a in assignments}

    def shown(item: PolicyItem) -> bool:
        return (
            role in item.applies_to
            and (not item.files or any(item.covers(path) for path in files))
            and (not item.when_found or any(item_id in found for item_id in item.when_found))
        )

    notes_only = [
        requirement.id
        for a in earlier
        for requirement in a.requirements
        if requirement.feedback is Feedback.NOTES_ONLY and requirement.id in found
    ]

    def filled(rule: ReviewRule) -> bool:
        return bool(notes_only) or not any(NOTES_ONLY in text for text in rule.texts)

    in_rules = in_effect([course.rules.rules, *(a.review_rules.rules for a in earlier)], passed)
    in_rules = [rule for rule in in_rules if shown(rule) and filled(rule)]
    rules = [rule for rule in in_rules if rule.shown_in is RulePlace.RULES]
    conditions = [rule for rule in in_rules if rule.shown_in is not RulePlace.RULES]
    examples: list[FixExample] = [
        example
        for example in in_effect(
            [course.rules.examples, *(a.review_rules.examples for a in earlier)], passed
        )
        if shown(example)
    ]
    edited = [
        suffix
        for suffix in course.profile.comment_style
        if any(path.endswith(suffix) for path in files)
    ]
    numbers = {rule.id: number for number, rule in enumerate(rules, start=1)}

    def fill(text: str) -> str:
        listed = ", ".join(f"`{requirement_id}`" for requirement_id in notes_only)
        return _fill(text, numbers, course, assignment).replace(NOTES_ONLY, listed)

    shown_examples = [ShownExample(example.situation, None, example.plan) for example in examples]
    shown_examples += [
        ShownExample(
            "", numbers[rule.id], json.dumps(rule.example.model_dump(), ensure_ascii=False)
        )
        for rule in rules
        if rule.example is not None
    ]
    guarded = rules + conditions
    return RolePolicy(
        rules=tuple(fill(rule.text_for(edited)) for rule in rules),
        allowed=tuple(fill(rule.never_a_violation) for rule in rules if rule.never_a_violation),
        clarifications=tuple(fill(rule.clarification) for rule in rules if rule.clarification),
        examples=tuple(shown_examples),
        detectors=tuple(
            Scoped(DetectorGrant(detector, rule.named_by), rule.files)
            for rule in guarded
            for detector in rule.detectors
        ),
        approved_notes=tuple(
            Scoped(note, rule.files) for rule in guarded for note in rule.approved_notes
        ),
        forbidden=tuple(
            Scoped(text, rule.files) for rule in guarded for text in rule.forbidden_text
        ),
        forbidden_changes=tuple(
            Scoped(text, rule.files) for rule in guarded for text in rule.forbidden_change
        ),
        fix_conditions=tuple(fill(rule.text_for(edited)) for rule in conditions),
        note_sections=tuple(
            Scoped(rule.note_section, rule.files)
            for rule in guarded
            if rule.note_section is not None
        ),
    )


def _fill(text: str, numbers: dict[str, int], course: Course, assignment: Assignment) -> str:
    taught = assignment.taught_techniques
    so_far = course.profile.taught_so_far.replace(TECHNIQUES, ", ".join(taught)) if taught else ""
    text = text.replace(SHORT_NAME, assignment.short_name).replace(TAUGHT_SO_FAR, so_far)
    return RULE_REFERENCE.sub(lambda match: str(numbers.get(match[1], f"'{match[1]}'")), text)
