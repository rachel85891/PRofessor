"""Structured replies: the schema in the prompt, JSON extraction and validation."""

import json

import pytest
from pydantic import BaseModel

from hw_checker.adapters.llm_copilot_sdk.replies import (
    UnreadableReplyError,
    feedback,
    read_reply,
    strict_schema,
    with_reply_format,
)


class Evidence(BaseModel):
    file: str
    line: int


class Finding(BaseModel):
    title: str
    severity: str = "minor"
    evidence: list[Evidence]
    note: str | None = None


def test_the_strict_schema_closes_every_object_and_requires_every_property() -> None:
    schema = strict_schema(Finding)

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["title", "severity", "evidence", "note"]
    evidence = schema["$defs"]["Evidence"]
    assert (evidence["additionalProperties"], evidence["required"]) == (False, ["file", "line"])


def test_the_strict_schema_leaves_the_models_own_schema_alone() -> None:
    strict_schema(Finding)

    assert "additionalProperties" not in Finding.model_json_schema()


@pytest.mark.parametrize(
    "content",
    [
        '{"title": "t", "evidence": [{"file": "a.cs", "line": 3}]}',
        '```json\n{"title": "t", "evidence": [{"file": "a.cs", "line": 3}]}\n```',
        'Here it is:\n{"title": "t", "evidence": [{"file": "a.cs", "line": 3}]}\nDone.',
        '<invoke name="bash">{"command": "echo"}</invoke>\nThe plan:\n\n```json\n'
        '{"title": "t", "evidence": [{"file": "a.cs", "line": 3}]}\n```',
        '```json\n{"title": "t", "evidence": [{"file": "a.cs", "line": 3}]}\n```\n'
        "```csharp\nvar x = new { a = 1 };\n```",
    ],
    ids=["alone", "fenced", "in-text", "text-with-braces-first", "code-block-after"],
)
def test_the_json_object_is_found_and_validated(content: str) -> None:
    finding = read_reply(content, Finding)

    assert finding == Finding(title="t", evidence=[Evidence(file="a.cs", line=3)])


@pytest.mark.parametrize("content", ["", "no json here", "[1, 2]", "{not json}", '"text"'])
def test_a_reply_without_a_json_object_is_unreadable(content: str) -> None:
    with pytest.raises(UnreadableReplyError, match="no JSON object"):
        read_reply(content, Finding)


def test_an_object_that_does_not_fit_names_the_fields_but_not_the_values() -> None:
    content = '{"title": "secret student text", "evidence": [{"file": "a.cs", "line": "x"}]}'

    with pytest.raises(UnreadableReplyError) as raised:
        read_reply(content, Finding)

    problems = raised.value.problems
    assert "evidence.0.line" in problems
    assert "secret student text" not in problems
    assert "secret student text" not in feedback(raised.value)


def test_the_reply_format_follows_the_prompt_with_the_strict_schema() -> None:
    text = with_reply_format("Synthetic prompt.", Finding)

    assert text.startswith("Synthetic prompt.\n\n# Reply format\n")
    assert "```json code block" in text
    assert json.dumps(strict_schema(Finding), separators=(",", ":")) in text


def test_the_feedback_asks_again_for_one_object_and_names_the_problems() -> None:
    text = feedback(UnreadableReplyError("title: Field required"))

    assert "title: Field required" in text
    assert "only one JSON object" in text
