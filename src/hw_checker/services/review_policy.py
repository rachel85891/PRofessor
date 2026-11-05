"""The review policy one role sees for one assignment (ADR 0023, design §8.1 and §8.2).

The course's rules and examples come first, then each assignment's up to the checked one,
by `order`; `replaces`, `after` and `until` shape the list, and each role keeps only the
items that apply to it. The rules are numbered from 1 in that order, and their texts'
placeholders are filled here, so a prompt's frame only shows them.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from hw_checker.domain import (
    ApprovedNote,
    Assignment,
    Course,
    FixExample,
    ForbiddenText,
    ReviewRole,
    SafeFix,
)
from hw_checker.domain.course import RULE_REFERENCE, SHORT_NAME, TAUGHT_SO_FAR, in_effect

TECHNIQUES = "{techniques}"
"""In the course's `taught_so_far`: the taught techniques, comma-separated."""


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
    detectors: frozenset[SafeFix]
    approved_notes: tuple[ApprovedNote, ...]
    forbidden: tuple[ForbiddenText, ...]

    def slots(self) -> dict[str, object]:
        """The values a prompt's frame shows."""
        return {
            "rules": list(self.rules),
            "allowed": list(self.allowed),
            "clarifications": list(self.clarifications),
            "examples": list(self.examples),
        }


def role_policy(
    course: Course,
    assignment: Assignment,
    assignments: Sequence[Assignment],
    role: ReviewRole,
) -> RolePolicy:
    """The policy `role` sees when `assignment` is checked; `assignments` are the round's
    (`_general` and 1..N)."""
    earlier = sorted((a for a in assignments if a.order <= assignment.order), key=lambda a: a.order)
    passed = {a.id: a.order < assignment.order for a in assignments}
    rules = [
        rule
        for rule in in_effect(
            [course.rules.rules, *(a.review_rules.rules for a in earlier)], passed
        )
        if role in rule.applies_to
    ]
    examples: list[FixExample] = [
        example
        for example in in_effect(
            [course.rules.examples, *(a.review_rules.examples for a in earlier)], passed
        )
        if role in example.applies_to
    ]
    numbers = {rule.id: number for number, rule in enumerate(rules, start=1)}

    def fill(text: str) -> str:
        return _fill(text, numbers, course, assignment)

    shown = [ShownExample(example.situation, None, example.plan) for example in examples]
    shown += [
        ShownExample(
            "", numbers[rule.id], json.dumps(rule.example.model_dump(), ensure_ascii=False)
        )
        for rule in rules
        if rule.example is not None
    ]
    return RolePolicy(
        rules=tuple(fill(rule.text) for rule in rules),
        allowed=tuple(fill(rule.never_a_violation) for rule in rules if rule.never_a_violation),
        clarifications=tuple(fill(rule.clarification) for rule in rules if rule.clarification),
        examples=tuple(shown),
        detectors=frozenset(detector for rule in rules for detector in rule.detectors),
        approved_notes=tuple(note for rule in rules for note in rule.approved_notes),
        forbidden=tuple(text for rule in rules for text in rule.forbidden_text),
    )


def _fill(text: str, numbers: dict[str, int], course: Course, assignment: Assignment) -> str:
    taught = assignment.taught_techniques
    so_far = course.profile.taught_so_far.replace(TECHNIQUES, ", ".join(taught)) if taught else ""
    text = text.replace(SHORT_NAME, assignment.short_name).replace(TAUGHT_SO_FAR, so_far)
    return RULE_REFERENCE.sub(lambda match: str(numbers.get(match[1], f"'{match[1]}'")), text)
