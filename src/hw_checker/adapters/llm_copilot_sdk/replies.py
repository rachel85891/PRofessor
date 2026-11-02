"""Structured replies: the schema sent with a prompt, and reading the reply back.

The SDK sends a JSON schema as the model's `responseFormat` (strict), but the runtime
neither parses nor validates the reply, so the reply is read and validated here.
"""

import copy
import json
from typing import Any

from pydantic import BaseModel, ValidationError

MAX_FEEDBACK_ERRORS = 10
FEEDBACK_TEMPLATE = (
    "Your previous reply could not be read as the required JSON object: {problems}. "
    "Reply again with only one JSON object that matches the schema."
)
"""The one retry after an unreadable reply; it names the problems, never the reply."""


class UnreadableReplyError(ValueError):
    """The reply is not one JSON object valid for the output type.

    `problems` describes what is wrong without quoting the reply.
    """

    def __init__(self, problems: str) -> None:
        super().__init__(problems)
        self.problems = problems


def strict_schema(output_type: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of `output_type` in the closed form strict structured output needs.

    Every object lists all its properties as required and allows no others; a field
    with a default is still sent by the model, and Pydantic validates it as usual.
    """
    schema = copy.deepcopy(output_type.model_json_schema())
    _close_objects(schema)
    return schema


def read_reply[OutputT: BaseModel](content: str, output_type: type[OutputT]) -> OutputT:
    """Validate the JSON object in `content` into `output_type`.

    Accepts the object alone, in a Markdown code fence, or surrounded by text.

    Raises:
        UnreadableReplyError: there is no JSON object, or it does not fit `output_type`.
    """
    text = _json_text(content)
    if text is None:
        raise UnreadableReplyError("the reply holds no JSON object")
    try:
        return output_type.model_validate_json(text)
    except ValidationError as error:
        raise UnreadableReplyError(_describe(error)) from None


def feedback(error: UnreadableReplyError) -> str:
    """The follow-up prompt that asks once more for a valid object."""
    return FEEDBACK_TEMPLATE.format(problems=error.problems)


def _close_objects(node: object) -> None:
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
            node["additionalProperties"] = False
        for value in node.values():
            _close_objects(value)
    elif isinstance(node, list):
        for item in node:
            _close_objects(item)


def _json_text(content: str) -> str | None:
    stripped = content.strip()
    if _is_json_object(stripped):
        return stripped
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    candidate = stripped[start : end + 1]
    return candidate if _is_json_object(candidate) else None


def _is_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _describe(error: ValidationError) -> str:
    """The validation problems by field, without the input values (they quote the reply)."""
    items = error.errors(include_input=False, include_url=False, include_context=False)
    problems = [
        f"{'.'.join(str(part) for part in item['loc']) or 'the object'}: {item['msg']}"
        for item in items[:MAX_FEEDBACK_ERRORS]
    ]
    return "; ".join(problems)
