import pytest

from hw_checker.adapters.docker_runner.http_response import HttpResponse
from hw_checker.adapters.docker_runner.probe_variables import (
    capture_value,
    fill_body,
    fill_path,
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
    assert capture_value(FROM_ID, body(text)) == CaptureResult(name="userId", value="3")


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
    assert capture_value(capture, response) == CaptureResult(name="userId", failure=failure)


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
