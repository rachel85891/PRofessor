import json
import uuid

import pytest

from hw_checker.adapters.docker_runner.body_adapter import (
    adapt_body,
    convert_value,
    dummy_value,
    normalize_name,
)
from hw_checker.adapters.docker_runner.body_models import BodyModel, BodyProperty, ModelUnavailable
from hw_checker.domain import BodyFieldOutcome

MATCHED, CONVERTED, FILLED, UNMATCHED, NOT_CONVERTIBLE, BY_CONTAINS = (
    BodyFieldOutcome.MATCHED,
    BodyFieldOutcome.CONVERTED,
    BodyFieldOutcome.FILLED,
    BodyFieldOutcome.UNMATCHED,
    BodyFieldOutcome.NOT_CONVERTIBLE,
    BodyFieldOutcome.MATCHED_BY_CONTAINS,
)


def prop(
    name: str,
    type_name: str,
    *,
    nullable: bool = False,
    required: bool = False,
    email: bool = False,
) -> BodyProperty:
    return BodyProperty(name, type_name, nullable, required, email)


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
    _, unmatched = adapt_body('{"userName":"u","pwd":1}', USER)
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


# ---------------------------------------------------------------- alternatives and lenient names


def notes_of(
    body: dict[str, object], model: BodyModel
) -> list[tuple[str, str | None, str | None, BodyFieldOutcome]]:
    _, adaptation = adapt_body(json.dumps(body), model)
    return [(n.field, n.alternative, n.property_name, n.outcome) for n in adaptation.notes]


FAKE = "pw"
"""A made-up password value."""
EMAIL_LOGIN = BodyModel(
    "Login", (prop("Email", "string", required=True), prop("Password", "string"))
)
BOTH = BodyModel("Account", (prop("UserName", "string"), prop("Email", "string")))


def test_an_alternative_key_goes_to_the_one_field_the_model_has() -> None:
    sent, adaptation = adapt_body('{"userName|email":"a@b.c","password":"pw"}', EMAIL_LOGIN)

    assert json.loads(sent) == {"email": "a@b.c", "password": FAKE}
    assert [(n.field, n.alternative, n.property_name) for n in adaptation.notes] == [
        ("userName|email", "email", "Email"),
        ("password", None, "Password"),
    ]
    assert not adaptation.mismatched


def test_an_alternative_key_goes_to_every_field_the_model_has() -> None:
    sent, _ = adapt_body('{"userName|email":"a@b.c"}', BOTH)

    assert json.loads(sent) == {"userName": "a@b.c", "email": "a@b.c"}
    assert notes_of({"userName|email": "a@b.c"}, BOTH) == [
        ("userName|email", "userName", "UserName", MATCHED),
        ("userName|email", "email", "Email", MATCHED),
    ]


def test_an_alternative_key_with_no_field_is_unmatched_and_not_sent() -> None:
    sent, adaptation = adapt_body('{"login|nick":"x"}', EMAIL_LOGIN)

    assert "login|nick" not in json.loads(sent)
    assert (adaptation.notes[0].field, adaptation.notes[0].outcome) == ("login|nick", UNMATCHED)
    assert adaptation.mismatched


@pytest.mark.parametrize(
    "property_name", ["LogInPassword", "l_password", "logpassword", "Password2"]
)
def test_password_matches_a_property_that_contains_it(property_name: str) -> None:
    model = BodyModel("Login", (prop("LogInEmail", "string"), prop(property_name, "string")))

    sent, adaptation = adapt_body('{"password":"pw"}', model)

    (note,) = adaptation.notes
    assert (note.property_name, note.outcome) == (property_name, BY_CONTAINS)
    assert json.loads(sent) == {property_name[0].lower() + property_name[1:]: "pw"}
    assert not adaptation.mismatched


def test_email_matches_by_contains_inside_an_alternative_key() -> None:
    model = BodyModel(
        "Login",
        (
            prop("logemail", "string", required=True, email=True),
            prop("logpassword", "string", required=True),
        ),
    )

    sent, adaptation = adapt_body('{"userName|email":"a@b.c","password":"pw"}', model)

    assert json.loads(sent) == {
        "logemail": "a@b.c",
        "logpassword": FAKE,
    }
    assert [(n.alternative, n.property_name, n.outcome) for n in adaptation.notes] == [
        ("email", "logemail", BY_CONTAINS),
        (None, "logpassword", BY_CONTAINS),
    ]


def test_several_containing_properties_are_never_guessed_between() -> None:
    model = BodyModel("Person", (prop("FirstName", "string"), prop("LastName", "string")))

    _, adaptation = adapt_body('{"name":"Dana"}', model)

    (note,) = adaptation.notes
    assert note.outcome is UNMATCHED
    assert note.candidates == ("FirstName", "LastName")
    assert adaptation.mismatched


def test_an_exact_name_wins_over_containing_ones() -> None:
    model = BodyModel("Person", (prop("Name", "string"), prop("LastName", "string")))

    assert notes_of({"name": "Dana"}, model) == [("name", None, "Name", MATCHED)]


@pytest.mark.parametrize(
    ("name", "normalized"),
    [("LogInPassword", "loginpassword"), ("l_password", "lpassword"), ("e-mail2", "email")],
)
def test_names_are_normalized(name: str, normalized: str) -> None:
    assert normalize_name(name) == normalized


@pytest.mark.parametrize(
    ("property_", "dummy"),
    [
        (prop("Contact", "string", email=True), "probe-contact@example.com"),
        (prop("LogInEmail", "string"), "probe-loginemail@example.com"),
        (prop("user_e_mail", "string"), "probe-user_e_mail@example.com"),
        (prop("Mailbox", "string"), "probe-mailbox"),
        (prop("Name", "string"), "probe-name"),
    ],
)
def test_email_properties_get_an_email_dummy(property_: BodyProperty, dummy: str) -> None:
    assert dummy_value(property_) == dummy
