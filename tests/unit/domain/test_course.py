"""The course profile and review rule models (ADR 0023): validation and inheritance."""

from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.domain import CommentStyle, ForbiddenText, ReviewRuleSet
from hw_checker.domain.course import in_effect
from tests.unit.domain import builders


def ids(rules: list[Any]) -> list[str]:
    return [rule.id for rule in rules]


def test_later_rules_follow_the_earlier_files() -> None:
    a, b, c = (builders.review_rule(id=name) for name in "abc")

    assert ids(in_effect([[a, b], [c]], {})) == ["a", "b", "c"]


def test_a_rule_placed_after_another_follows_it() -> None:
    a, b = builders.review_rule(id="a"), builders.review_rule(id="b")
    c = builders.review_rule(id="c", after="a")
    d = builders.review_rule(id="d", after="c")

    assert ids(in_effect([[a, b], [c, d]], {})) == ["a", "c", "d", "b"]


def test_a_rule_that_replaces_another_takes_its_place() -> None:
    a, b = builders.review_rule(id="a"), builders.review_rule(id="b")
    new_a = builders.review_rule(id="a2", replaces="a", text="Changed.")

    rules = in_effect([[a, b], [new_a]], {})

    assert ids(rules) == ["a2", "b"]
    assert rules[0].text == "Changed."


def test_a_rule_stops_after_its_until_assignment() -> None:
    a = builders.review_rule(id="a", until="hw01")
    b = builders.review_rule(id="b", until="hw02")

    assert ids(in_effect([[a, b]], {"hw01": True, "hw02": False})) == ["b"]
    assert ids(in_effect([[a, b]], {})) == ["a", "b"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"applies_to": ["fixer", "teacher"]}, "applies_to"),
        ({"applies_to": []}, "applies_to"),
        ({"kind": "always"}, "kind"),
        ({"detectors": ["missing_break"]}, "only a fix_allowed rule names detectors"),
        ({"kind": "fix_allowed", "detectors": ["rename_everything"]}, "detectors"),
        ({"status": "maybe"}, "status"),
        ({"origin": {"source": "rumor"}}, "origin"),
        ({"colour": "red"}, "colour"),
    ],
)
def test_an_invalid_rule_is_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        builders.review_rule(**overrides)


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"message": "m"}, "exactly one of text and pattern"),
        ({"text": "x", "pattern": "x", "message": "m"}, "exactly one of text and pattern"),
        ({"pattern": "(", "message": "m"}, "not a valid regular expression"),
    ],
)
def test_a_forbidden_text_is_one_text_or_one_pattern(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ForbiddenText.model_validate(values)


def test_a_forbidden_literal_is_matched_as_written() -> None:
    forbidden = ForbiddenText(text="BadRequest(", message="m")

    assert forbidden.regex.search("return BadRequest();")
    assert not forbidden.regex.search("BadRequestX")


def test_the_fix_marker_allows_spaces_and_any_case() -> None:
    style = CommentStyle(line="//", fix_marker="//fix:")

    assert style.marker_pattern.match("// FIX: added")
    assert not style.marker_pattern.match("//fixed it")
    with pytest.raises(ValidationError, match="fix_marker must start with"):
        CommentStyle(line="#", fix_marker="//fix:")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s[:3], "missing files"),
        (lambda s: [*s, s[0]], "repeated fixed"),
        (lambda s: [{"id": "fixed", "heading": "Fixed"}, *s[1:]], "needs a label and an intro"),
        (lambda s: [{**s[0], "id": "extras"}, *s[1:]], "sections"),
    ],
    ids=["missing", "repeated", "writer-section-without-intro", "unknown-id"],
)
def test_the_pr_sections_are_each_known_section_once(change: Any, message: str) -> None:
    pr_format = builders.course_profile().pr_format.model_dump(mode="json")
    pr_format["sections"] = change(pr_format["sections"])

    with pytest.raises(ValidationError, match=message):
        builders.course_profile(pr_format=pr_format)


def test_comment_styles_are_keyed_by_file_suffix() -> None:
    with pytest.raises(ValidationError, match="file suffixes"):
        builders.course_profile(comment_style={"csharp": {"line": "//", "fix_marker": "//fix:"}})

    profile = builders.course_profile()
    assert profile.style_for("Controllers/A.cs") is not None
    assert profile.style_for("wwwroot/index.html") is None


def test_drafts_are_listed() -> None:
    rules = ReviewRuleSet(
        rules=(builders.review_rule(id="a"), builders.review_rule(id="b", status="draft")),
    )

    assert rules.drafts == ("rule 'b'",)
