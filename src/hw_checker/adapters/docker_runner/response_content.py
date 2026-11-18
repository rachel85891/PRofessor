"""What a probe's JSON response holds: its shape, and which expected values are missing.

Status codes alone miss real problems: a GET that still returns the project template's
`"value1", "value2"` answers 200. A probe may therefore expect a shape (`object`,
`array_of_objects`) and values that must appear somewhere in the response; this module
reads both from the full, bounded response body.
"""

import json
import re

from hw_checker.adapters.docker_runner.http_response import HttpResponse
from hw_checker.domain import ResponseShape
from hw_checker.domain.execution import MAX_TYPE_NAME_CHARS

BAD_REQUEST = 400
ROOT_KEYS = frozenset({"$", ""})
"""The `errors` keys ASP.NET uses for the body as a whole, not one of its fields."""
NOT_CONVERTED = "could not be converted"
_TARGET_TYPE = re.compile(r"could not be converted to ([\w.`\[\], +<>]+?)\.?\s+Path:", re.I)
_NAMESPACE = re.compile(r"\b(?:\w+\.)+(?=\w)")


def response_shape(response: HttpResponse) -> ResponseShape:
    """The shape of the response body; `cut` when the body hit the size limit."""
    if response.truncated:
        return ResponseShape.CUT
    if not response.body.strip():
        return ResponseShape.EMPTY
    try:
        data = json.loads(response.body)
    except ValueError:
        return ResponseShape.NOT_JSON
    if isinstance(data, dict):
        return ResponseShape.OBJECT
    if isinstance(data, list):
        if not data:
            return ResponseShape.EMPTY_ARRAY
        if all(isinstance(item, dict) for item in data):
            return ResponseShape.ARRAY_OF_OBJECTS
        return ResponseShape.ARRAY_OF_SCALARS
    return ResponseShape.SCALAR


def missing_values(body: str, expected: list[str]) -> tuple[str, ...]:
    """The expected values that appear nowhere in the JSON `body`, in their given order.

    Values are compared after trimming; text ignoring case, numbers and booleans as their
    JSON text. A body that is not JSON contains none of them.
    """
    if not expected:
        return ()
    try:
        data = json.loads(body)
    except ValueError:
        return tuple(expected)
    present = set(_values(data))
    return tuple(value for value in expected if _normalize(value) not in present)


def _values(data: object) -> list[str]:
    if isinstance(data, dict):
        return [value for item in data.values() for value in _values(item)]
    if isinstance(data, list):
        return [value for item in data for value in _values(item)]
    text = _as_text(data)
    return [] if text is None else [_normalize(text)]


def _as_text(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return None


def _normalize(text: str) -> str:
    return text.strip().casefold()


def body_rejection(response: HttpResponse) -> str | None:
    """Whether the app rejected the body as a whole: None when it did not, otherwise the
    type the body did not fit, without namespaces (empty when the error does not name it).

    ASP.NET answers a JSON body that does not fit the action's parameter type (a string
    sent where an object is expected) with a 400 validation problem whose `errors` hold
    "could not be converted" under the root path `$`. An error on a named field only (a
    property that could not be converted) is not a whole-body rejection.
    """
    if response.status != BAD_REQUEST or response.truncated:
        return None
    try:
        data = json.loads(response.body)
    except ValueError:
        return None
    errors = data.get("errors") if isinstance(data, dict) else None
    if not isinstance(errors, dict):
        return None
    messages = [
        message
        for key, values in errors.items()
        if key in ROOT_KEYS and isinstance(values, list)
        for message in values
        if isinstance(message, str) and NOT_CONVERTED in message.casefold()
    ]
    if not messages:
        return None
    match = _TARGET_TYPE.search(messages[0])
    return "" if match is None else _NAMESPACE.sub("", match.group(1))[:MAX_TYPE_NAME_CHARS]
