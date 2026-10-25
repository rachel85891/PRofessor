"""Adapts a probe's intended JSON body to the student's own body model.

The probe body in requirements.yaml says what a request means ("create user newuser with
password 12345"); each student's model decides its shape. Every body key is matched to a
model property ignoring case, values are converted to the property's type when that is
safe, required properties the body does not mention get deterministic dummy values, and
every step is recorded, so the reviewer sees exactly what was sent and why.

A key may list alternatives, `userName|email`: the value goes to every alternative the model
has. A key (or alternative) with no property of that name falls back to lenient matching:
with case, `_`, `-` and digits ignored, the one property whose name contains it
(`password` for `LogInPassword`); several such properties are never guessed between.
"""

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from hw_checker.adapters.docker_runner.body_models import BodyModel, BodyProperty, ModelUnavailable
from hw_checker.domain import BodyAdaptation, BodyFieldNote, BodyFieldOutcome

INTEGER_TYPES = frozenset({"int", "long", "short", "byte", "uint", "ulong", "ushort", "sbyte"})
NUMBER_TYPES = frozenset({"double", "float", "decimal"})
STRING_TYPES = frozenset({"string", "char"})
DATE_TYPES = frozenset({"DateTime", "DateTimeOffset", "DateOnly"})
DUMMY_DATE = date(2025, 1, 1)
"""A fixed date rather than today, so reruns send the same body."""

ALTERNATIVE_SEPARATOR = "|"
_IGNORED_IN_NAMES = re.compile(r"[_\-\d]")
_INTEGER_TEXT = re.compile(r"^\s*-?\d+\s*$")
_NUMBER_TEXT = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")
_NOT_CONVERTIBLE = object()


def adapt_body(body: str, model: BodyModel | ModelUnavailable) -> tuple[str, BodyAdaptation]:
    """The body to send, and how it was adapted to `model`.

    When the model is unavailable, or the body is not a JSON object, the body is sent as
    written and the adaptation says why.
    """
    if isinstance(model, ModelUnavailable):
        return body, BodyAdaptation(adapted=False, reason=model.reason)
    try:
        intended = json.loads(body)
    except ValueError:
        return body, BodyAdaptation(
            adapted=False, model=model.class_name, reason="body is not JSON"
        )
    if not isinstance(intended, dict):
        return body, BodyAdaptation(
            adapted=False, model=model.class_name, reason="body is not a JSON object"
        )
    sent: dict[str, object] = {}
    notes: list[BodyFieldNote] = []
    covered: set[str] = set()
    for key, value in intended.items():
        has_alternatives = ALTERNATIVE_SEPARATOR in key
        matches, candidates = match_key(key, model.properties)
        if not matches:
            if not has_alternatives:
                sent[key] = value  # a plain key is kept, as it may still bind
            notes.append(
                BodyFieldNote(field=key, outcome=BodyFieldOutcome.UNMATCHED, candidates=candidates)
            )
            continue
        for match in matches:
            prop = match.prop
            covered.add(prop.name)
            name = key if not (has_alternatives or match.by_contains) else _json_name(prop.name)
            converted = convert_value(value, prop)
            if converted is _NOT_CONVERTIBLE:
                sent[name] = dummy_value(prop)
                outcome = BodyFieldOutcome.NOT_CONVERTIBLE
            else:
                sent[name] = converted
                outcome = (
                    BodyFieldOutcome.MATCHED_BY_CONTAINS
                    if match.by_contains
                    else BodyFieldOutcome.MATCHED
                    if _same_json(converted, value)
                    else BodyFieldOutcome.CONVERTED
                )
            notes.append(
                BodyFieldNote(
                    field=key,
                    property_name=prop.name,
                    outcome=outcome,
                    alternative=match.alternative if has_alternatives else None,
                )
            )
    for prop in model.properties:
        if prop.required and prop.name not in covered:
            sent[_json_name(prop.name)] = dummy_value(prop)
            notes.append(
                BodyFieldNote(
                    field=prop.name, property_name=prop.name, outcome=BodyFieldOutcome.FILLED
                )
            )
    text = json.dumps(sent, ensure_ascii=False, separators=(",", ":"))
    return text, BodyAdaptation(adapted=True, model=model.class_name, notes=tuple(notes))


@dataclass(frozen=True)
class KeyMatch:
    """A property a body key (or one of its alternatives) was matched to."""

    alternative: str
    prop: BodyProperty
    by_contains: bool


def match_key(
    key: str, properties: tuple[BodyProperty, ...]
) -> tuple[list[KeyMatch], tuple[str, ...]]:
    """The properties `key` matches, each once, and the candidates of unresolved alternatives.

    Each alternative of `a|b|c` matches a property of the same name, ignoring case; failing
    that, the single property whose normalized name contains the alternative's normalized
    name. When several properties contain it, it matches nothing and they are returned as
    candidates.
    """
    by_name = {prop.name.lower(): prop for prop in properties}
    matches: list[KeyMatch] = []
    candidates: list[str] = []
    for alternative in (part.strip() for part in key.split(ALTERNATIVE_SEPARATOR)):
        if not alternative:
            continue
        exact = by_name.get(alternative.lower())
        if exact is not None:
            found = [KeyMatch(alternative, exact, by_contains=False)]
        else:
            wanted = normalize_name(alternative)
            containing = [p for p in properties if wanted and wanted in normalize_name(p.name)]
            if len(containing) > 1:
                candidates += [p.name for p in containing if p.name not in candidates]
            found = (
                [KeyMatch(alternative, containing[0], by_contains=True)]
                if len(containing) == 1
                else []
            )
        matches += [m for m in found if all(m.prop != other.prop for other in matches)]
    return matches, tuple(candidates)


def normalize_name(name: str) -> str:
    """A name in lower case without `_`, `-` and digits, for lenient matching."""
    return _IGNORED_IN_NAMES.sub("", name.lower())


def convert_value(value: object, prop: BodyProperty) -> object:
    """`value` as the property's JSON type, or `_NOT_CONVERTIBLE` when that is not safe."""
    if value is None:
        return None if prop.nullable or prop.type_name not in _VALUE_KINDS else _NOT_CONVERTIBLE
    kind = prop.type_name
    if kind in STRING_TYPES:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return str(value)
        return _NOT_CONVERTIBLE
    if kind in INTEGER_TYPES:
        if isinstance(value, bool):
            return _NOT_CONVERTIBLE
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and _INTEGER_TEXT.match(value):
            return int(value)
        return _NOT_CONVERTIBLE
    if kind in NUMBER_TYPES:
        if isinstance(value, bool):
            return _NOT_CONVERTIBLE
        if isinstance(value, int | float):
            return value
        if isinstance(value, str) and _NUMBER_TEXT.match(value):
            number = float(value)
            return int(number) if number.is_integer() and "." not in value else number
        return _NOT_CONVERTIBLE
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return _NOT_CONVERTIBLE
    if kind in DATE_TYPES:
        return value if isinstance(value, str) and _is_date(value) else _NOT_CONVERTIBLE
    if kind == "Guid":
        return value if isinstance(value, str) and _is_guid(value) else _NOT_CONVERTIBLE
    return value  # an enum, a nested class or another type: sent as written


def dummy_value(prop: BodyProperty) -> object:
    """A deterministic, type-appropriate value for a property the body cannot fill."""
    kind = prop.type_name
    if kind == "string" and (prop.email or "email" in normalize_name(prop.name)):
        return f"probe-{prop.name.lower()}@example.com"  # passes [EmailAddress]
    if kind == "string":
        return f"probe-{prop.name.lower()}"
    if kind == "char":
        return "x"
    if kind in INTEGER_TYPES:
        return 1
    if kind in NUMBER_TYPES:
        return 1.0
    if kind == "bool":
        return False
    if kind == "DateOnly":
        return DUMMY_DATE.isoformat()
    if kind in DATE_TYPES:
        return datetime(DUMMY_DATE.year, DUMMY_DATE.month, DUMMY_DATE.day).isoformat()
    if kind == "Guid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"hw-checker/{prop.name.lower()}"))
    return None


_VALUE_KINDS = INTEGER_TYPES | NUMBER_TYPES | DATE_TYPES | {"bool", "char", "Guid"}


def _same_json(converted: object, original: object) -> bool:
    return type(converted) is type(original) and converted == original


def _json_name(property_name: str) -> str:
    """ASP.NET Core's default camelCase name of a property."""
    return property_name[:1].lower() + property_name[1:]


def _is_date(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_guid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
