import json
import uuid

import pytest

from hw_checker.adapters.docker_runner.body_adapter import adapt_body, convert_value, dummy_value
from hw_checker.adapters.docker_runner.body_models import BodyModel, BodyProperty, ModelUnavailable
from hw_checker.domain import BodyFieldOutcome

MATCHED, CONVERTED, FILLED, UNMATCHED, NOT_CONVERTIBLE = (
    BodyFieldOutcome.MATCHED,
    BodyFieldOutcome.CONVERTED,
    BodyFieldOutcome.FILLED,
    BodyFieldOutcome.UNMATCHED,
    BodyFieldOutcome.NOT_CONVERTIBLE,
)


def prop(
    name: str, type_name: str, *, nullable: bool = False, required: bool = False
) -> BodyProperty:
    return BodyProperty(name, type_name, nullable, required)


USER = BodyModel(
    "User",
    (
        prop("Id", "int"),
        prop("UserName", "string", required=True),
        prop("Password", "int", required=True),
        prop("Name", "string", required=True),
        prop("LastName", "string", required=True),
        prop("IsAdmin", "bool"),
        prop("Nickname", "string", nullable=True),
    ),
)


def adapted(
    body: dict[str, object], model: BodyModel = USER
) -> tuple[dict[str, object], list[tuple[str, BodyFieldOutcome]]]:
    text, adaptation = adapt_body(json.dumps(body), model)
    assert adaptation.adapted and adaptation.model == model.class_name
    return json.loads(text), [(note.field, note.outcome) for note in adaptation.notes]


def test_every_note_kind() -> None:
    sent, notes = adapted(
        {"userName": "newuser", "password": "12345", "isAdmin": "maybe", "email": "a@b.c"}
    )

    assert notes == [
        ("userName", MATCHED),
        ("password", CONVERTED),
        ("isAdmin", NOT_CONVERTIBLE),
        ("email", UNMATCHED),
        ("Name", FILLED),
        ("LastName", FILLED),
    ]
    assert sent == {
        "userName": "newuser",
        "password": 12345,
        "isAdmin": False,
        "email": "a@b.c",
        "name": "probe-name",
        "lastName": "probe-lastname",
    }


def test_keys_match_properties_ignoring_case() -> None:
    sent, notes = adapted({"USERNAME": "u", "PassWord": 7, "name": "n", "lastname": "l"})

    assert [outcome for _, outcome in notes] == [MATCHED] * 4
    assert sent == {"USERNAME": "u", "PassWord": 7, "name": "n", "lastname": "l"}


def test_mismatch_is_reported_only_for_unmatched_or_unconvertible_fields() -> None:
    _, filled_only = adapt_body('{"userName":"u","password":1}', USER)
    _, unmatched = adapt_body('{"userName":"u","pass":1}', USER)
    _, unconvertible = adapt_body('{"userName":"u","password":"secret"}', USER)

    assert not filled_only.mismatched
    assert unmatched.mismatched and unconvertible.mismatched


def test_dummies_are_deterministic() -> None:
    first, _ = adapt_body('{"userName":"u"}', USER)
    second, _ = adapt_body('{"userName":"u"}', USER)

    assert first == second


@pytest.mark.parametrize(
    ("value", "type_name", "expected"),
    [
        ("12345", "int", 12345),
        (" -3 ", "long", -3),
        (4.0, "int", 4),
        (12, "string", "12"),
        (True, "string", "true"),
        ("2.5", "decimal", 2.5),
        ("7", "double", 7),
        ("TRUE", "bool", True),
        ("2025-03-01", "DateTime", "2025-03-01"),
        ("2025-03-01T10:00:00Z", "DateTimeOffset", "2025-03-01T10:00:00Z"),
        ("3f2504e0-4f89-11d3-9a0c-0305e82c3301", "Guid", "3f2504e0-4f89-11d3-9a0c-0305e82c3301"),
        ("Admin", "UserRole", "Admin"),
    ],
)
def test_safe_conversions(value: object, type_name: str, expected: object) -> None:
    assert convert_value(value, prop("P", type_name)) == expected


@pytest.mark.parametrize(
    ("value", "type_name", "nullable"),
    [
        ("secret", "int", False),
        (True, "int", False),
        (2.5, "int", False),
        ("yes", "bool", False),
        ("next week", "DateTime", False),
        ("not-a-guid", "Guid", False),
        (None, "int", False),
        ({"a": 1}, "string", False),
    ],
)
def test_unsafe_conversions_are_refused(value: object, type_name: str, nullable: bool) -> None:
    _, adaptation = adapt_body(
        json.dumps({"p": value}), BodyModel("M", (prop("P", type_name, nullable=nullable),))
    )

    assert [note.outcome for note in adaptation.notes] == [NOT_CONVERTIBLE]


def test_null_is_kept_for_nullable_and_reference_properties() -> None:
    model = BodyModel("M", (prop("Age", "int", nullable=True), prop("Bio", "string")))

    sent, notes = adapted({"age": None, "bio": None}, model)

    assert sent == {"age": None, "bio": None}
    assert [outcome for _, outcome in notes] == [MATCHED, MATCHED]


@pytest.mark.parametrize(
    ("type_name", "dummy"),
    [
        ("string", "probe-field"),
        ("int", 1),
        ("decimal", 1.0),
        ("bool", False),
        ("DateTime", "2025-01-01T00:00:00"),
        ("DateOnly", "2025-01-01"),
        ("Guid", str(uuid.uuid5(uuid.NAMESPACE_URL, "hw-checker/field"))),
        ("Address", None),
    ],
)
def test_dummy_values_by_type(type_name: str, dummy: object) -> None:
    value = dummy_value(prop("Field", type_name))

    assert value == dummy


def test_unavailable_model_sends_the_body_as_written() -> None:
    body = '{"userName":"u","password":"x"}'

    sent, adaptation = adapt_body(body, ModelUnavailable("class User not found in the project"))

    assert sent == body
    assert not adaptation.adapted
    assert adaptation.reason == "class User not found in the project"
    assert not adaptation.mismatched


@pytest.mark.parametrize(
    ("body", "reason"), [("not json", "body is not JSON"), ("[1, 2]", "not a JSON object")]
)
def test_body_that_is_not_an_object_is_sent_as_written(body: str, reason: str) -> None:
    sent, adaptation = adapt_body(body, USER)

    assert sent == body
    assert not adaptation.adapted and reason in adaptation.reason
