import pytest

from hw_checker.adapters.docker_runner.http_response import HttpResponse
from hw_checker.adapters.docker_runner.probe_variables import (
    capture_value,
    fill_body,
    fill_path,
    fill_text,
    not_sent,
)
from hw_checker.domain import CaptureFailure, CaptureResult, ProbeCapture

FROM_ID = ProbeCapture(name="userId", source="body:id")
FROM_LOCATION = ProbeCapture(name="userId", source="location")


def body(text: str) -> HttpResponse:
    return HttpResponse(201, {"content-type": "application/json"}, text)


@pytest.mark.parametrize(
    "text", ['{"id":3,"userName":"newuser"}', '{"Id":3}', '{"ID":3}', '{"id":"3"}', '{"id":3.0}']
)
def test_capture_from_a_body_field_ignoring_case(text: str) -> None:
    captured = capture_value(FROM_ID, body(text))

    assert (captured.value, captured.failure) == ("3", None)
    assert captured.source_field is not None and captured.source_field.lower() == "id"


@pytest.mark.parametrize("key", ["userId", "UserID", "user_id", "productId", "USER-ID", "user1Id"])
def test_an_id_like_field_is_captured_when_there_is_no_id(key: str) -> None:
    captured = capture_value(FROM_ID, body(f'{{"{key}":13,"userName":"newuser"}}'))

    assert captured == CaptureResult(name="userId", value="13", source_field=key)


def test_an_exact_id_wins_over_an_id_like_field() -> None:
    captured = capture_value(FROM_ID, body('{"userId":13,"id":4}'))

    assert (captured.value, captured.source_field) == ("4", "id")


@pytest.mark.parametrize(
    ("text", "candidates"),
    [
        ('{"userId":13,"roleId":2,"name":"x"}', ("userId", "roleId")),
        ('{"Id":1,"ID":2}', ("Id", "ID")),
    ],
)
def test_several_candidate_fields_are_ambiguous_never_guessed(
    text: str, candidates: tuple[str, ...]
) -> None:
    captured = capture_value(FROM_ID, body(text))

    assert captured == CaptureResult(
        name="userId", failure=CaptureFailure.AMBIGUOUS, candidates=candidates
    )


def test_fields_that_do_not_end_with_the_name_are_not_used() -> None:
    captured = capture_value(FROM_ID, body('{"identity":"x","idNumber":5}'))

    assert captured.failure is CaptureFailure.FIELD_MISSING


def test_exact_field_name_wins_over_other_cases() -> None:
    response = body('{"Id":1,"id":2}')

    assert capture_value(FROM_ID, response).value == "2"


@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("http://app:8080/api/Users/3", "3"),
        ("/api/users/7/", "7"),
        ("/api/users/abc-def?x=1", "abc-def"),
    ],
)
def test_capture_from_the_location_header(location: str, value: str) -> None:
    response = HttpResponse(201, {"location": location}, "")

    assert capture_value(FROM_LOCATION, response) == CaptureResult(name="userId", value=value)


@pytest.mark.parametrize(
    ("capture", "response", "failure"),
    [
        (FROM_ID, None, CaptureFailure.NO_RESPONSE),
        (FROM_ID, body(""), CaptureFailure.NO_JSON),
        (FROM_ID, body("created"), CaptureFailure.NO_JSON),
        (FROM_ID, body("[1, 2]"), CaptureFailure.NO_JSON),
        (FROM_ID, body('{"userName":"x"}'), CaptureFailure.FIELD_MISSING),
        (FROM_ID, body('{"id":null}'), CaptureFailure.NOT_A_VALUE),
        (FROM_ID, body('{"id":{"value":3}}'), CaptureFailure.NOT_A_VALUE),
        (FROM_ID, body('{"id":"' + "x" * 600 + '"}'), CaptureFailure.NOT_A_VALUE),
        (FROM_LOCATION, HttpResponse(201, {}, '{"id":3}'), CaptureFailure.NO_LOCATION),
        (FROM_LOCATION, HttpResponse(201, {"location": "/"}, ""), CaptureFailure.NO_LOCATION),
    ],
)
def test_every_capture_failure_reason(
    capture: ProbeCapture, response: HttpResponse | None, failure: CaptureFailure
) -> None:
    captured = capture_value(capture, response)

    assert (captured.name, captured.value, captured.failure) == ("userId", None, failure)


def test_a_probe_not_sent_captures_nothing() -> None:
    assert not_sent(FROM_ID) == CaptureResult(name="userId", failure=CaptureFailure.NOT_SENT)
    assert not_sent(None) is None


def test_values_are_filled_into_paths_url_encoded() -> None:
    variables = {"userId": "3", "name": "a b/c"}

    assert fill_path("/api/users/{userId}", variables) == "/api/users/3"
    assert fill_path("/api/find/{name}?id={userId}", variables) == "/api/find/a%20b%2Fc?id=3"


def test_values_are_filled_into_json_strings_escaped() -> None:
    variables = {"userId": "3", "quote": 'say "hi"'}

    assert fill_body('{"id":"{userId}","note":"{quote}"}', variables) == (
        '{"id":"3","note":"say \\"hi\\""}'
    )


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('{"userId":7}', "7"),
        ('{"userId":"7"}', "7"),
        (
            '{"userId":"3f2504e0-4f89-11d3-9a0c-0305e82c3301"}',
            "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        ),
    ],
)
def test_a_leniently_found_field_is_taken_when_it_holds_an_identifier(
    text: str, value: str
) -> None:
    captured = capture_value(FROM_ID, body(text))

    assert (captured.value, captured.source_field) == (value, "userId")


@pytest.mark.parametrize(
    "text", ['{"isPaid":false}', '{"isPaid":true,"name":"x"}', '{"userId":"abc"}', '{"userId":2.5}']
)
def test_a_leniently_found_field_that_is_not_an_identifier_is_refused(text: str) -> None:
    captured = capture_value(FROM_ID, body(text))

    assert captured.failure is CaptureFailure.NOT_AN_IDENTIFIER
    assert captured.source_field is not None and captured.value is None


def test_an_exact_or_same_case_field_is_taken_whatever_it_holds() -> None:
    assert capture_value(FROM_ID, body('{"id":"abc"}')).value == "abc"
    assert capture_value(FROM_ID, body('{"ID":"user-7"}')).value == "user-7"


def test_values_are_filled_into_text_as_they_are() -> None:
    assert fill_text("{name} <{userId}>", {"name": "a b/c", "userId": "3"}) == "a b/c <3>"
