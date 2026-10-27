"""Chained probes: values captured from one response and filled into later probes.

"Create a user, then read it back" needs the id the app assigned, and ids differ between
students. A probe can capture a value (`body:<field>` or `location`), and later probes use it
as `{name}` in their path or in string values of their body. Values are used as text; the
body adaptation converts them to the model's types afterwards.

A `body:<field>` capture takes the field of that exact name, then one differing only in
case, then the single field whose normalized name (as in body matching) ends with the
field's: `body:id` finds `userId`, `UserID` or `user_id`. Several such fields are never
guessed between, and a field found that way is only taken when it holds an identifier (an
integer, a string of digits or a GUID).
"""

import json
import re
import uuid
from urllib.parse import quote, urlsplit

from hw_checker.adapters.docker_runner.body_adapter import normalize_name
from hw_checker.adapters.docker_runner.http_response import HttpResponse
from hw_checker.domain import CaptureFailure, CaptureResult, ProbeCapture
from hw_checker.domain.assignment import VARIABLE_USE
from hw_checker.domain.execution import MAX_CAPTURED_CHARS


def fill_path(path: str, variables: dict[str, str]) -> str:
    """`path` with each `{name}` replaced by its URL-encoded value."""
    return VARIABLE_USE.sub(lambda match: quote(variables[match[1]], safe=""), path)


def fill_text(text: str, variables: dict[str, str]) -> str:
    """`text` with each `{name}` replaced by its value as it is."""
    return VARIABLE_USE.sub(lambda match: variables[match[1]], text)


def fill_body(body: str, variables: dict[str, str]) -> str:
    """`body` (JSON text) with each `{name}` replaced by its value, escaped for a JSON string."""
    return VARIABLE_USE.sub(lambda match: json.dumps(variables[match[1]])[1:-1], body)


def capture_value(capture: ProbeCapture, response: HttpResponse | None) -> CaptureResult:
    """The value `capture` takes from `response`, or why there is none."""
    if response is None:
        return CaptureResult(name=capture.name, failure=CaptureFailure.NO_RESPONSE)
    field = capture.body_field
    if field is None:
        return _from_location(capture.name, response)
    return _from_body(capture.name, field, response.body)


def not_sent(capture: ProbeCapture | None) -> CaptureResult | None:
    """The capture result of a probe that was not sent."""
    if capture is None:
        return None
    return CaptureResult(name=capture.name, failure=CaptureFailure.NOT_SENT)


def _from_location(name: str, response: HttpResponse) -> CaptureResult:
    location = response.header("location")
    segments = [] if not location else urlsplit(location).path.rstrip("/").split("/")
    if not segments or not segments[-1]:
        return CaptureResult(name=name, failure=CaptureFailure.NO_LOCATION)
    return CaptureResult(name=name, value=segments[-1])


def _from_body(name: str, field: str, body: str) -> CaptureResult:
    try:
        data = json.loads(body)
    except ValueError:
        return CaptureResult(name=name, failure=CaptureFailure.NO_JSON)
    if not isinstance(data, dict):
        return CaptureResult(name=name, failure=CaptureFailure.NO_JSON)
    keys, lenient = _matching_keys(field, list(data))
    if not keys:
        return CaptureResult(name=name, failure=CaptureFailure.FIELD_MISSING)
    if len(keys) > 1:
        return CaptureResult(name=name, failure=CaptureFailure.AMBIGUOUS, candidates=tuple(keys))
    (key,) = keys
    value = data[key]
    if lenient and not _is_identifier(value):
        return CaptureResult(name=name, failure=CaptureFailure.NOT_AN_IDENTIFIER, source_field=key)
    text = _as_text(value)
    if text is None or len(text) > MAX_CAPTURED_CHARS:
        return CaptureResult(name=name, failure=CaptureFailure.NOT_A_VALUE, source_field=key)
    return CaptureResult(name=name, value=text, source_field=key)


def _matching_keys(field: str, keys: list[str]) -> tuple[list[str], bool]:
    """The response keys for `field`, and whether they were found only leniently: the exact
    one, else those equal ignoring case, else (leniently) those whose normalized name ends
    with the field's normalized name."""
    if field in keys:
        return [field], False
    same_case = [key for key in keys if key.lower() == field.lower()]
    if same_case:
        return same_case, False
    wanted = normalize_name(field)
    if not wanted:
        return [], False
    return [key for key in keys if normalize_name(key).endswith(wanted)], True


_DIGITS = re.compile(r"^\s*\d+\s*$")


def _is_identifier(value: object) -> bool:
    """An integer, a string of digits or a GUID; never a boolean."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if not isinstance(value, str):
        return False
    if _DIGITS.match(value):
        return True
    try:
        uuid.UUID(value.strip())
    except ValueError:
        return False
    return True


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
