import pytest

from hw_checker.adapters.docker_runner.http_response import HttpResponse, parse_response
from hw_checker.adapters.docker_runner.response_content import missing_values, response_shape
from hw_checker.domain import ResponseShape


@pytest.mark.parametrize(
    ("body", "shape"),
    [
        ('{"id":3,"userName":"u"}', ResponseShape.OBJECT),
        ('[{"id":1},{"id":2}]', ResponseShape.ARRAY_OF_OBJECTS),
        ('["value1","value2"]', ResponseShape.ARRAY_OF_SCALARS),
        ('[{"id":1},"value2"]', ResponseShape.ARRAY_OF_SCALARS),
        ("[]", ResponseShape.EMPTY_ARRAY),
        ('"value"', ResponseShape.SCALAR),
        ("42", ResponseShape.SCALAR),
        ("true", ResponseShape.SCALAR),
        ("value", ResponseShape.NOT_JSON),
        ("<html>error</html>", ResponseShape.NOT_JSON),
        ("", ResponseShape.EMPTY),
        ("  \r\n ", ResponseShape.EMPTY),
    ],
)
def test_every_response_shape(body: str, shape: ResponseShape) -> None:
    assert response_shape(HttpResponse(200, {}, body)) == shape


def test_a_body_cut_at_the_limit_is_cut_whatever_it_holds() -> None:
    assert response_shape(HttpResponse(200, {}, '[{"id":1},{"i', truncated=True)) is (
        ResponseShape.CUT
    )


def test_output_reaching_the_limit_is_marked_truncated() -> None:
    raw = "HTTP/1.1 200 OK\r\n\r\n" + "x" * 100
    limit = len(raw)

    assert parse_response(raw, limit) == HttpResponse(200, {}, "x" * 100, truncated=True)
    assert parse_response(raw, limit + 1) == HttpResponse(200, {}, "x" * 100)
    assert parse_response(raw) == HttpResponse(200, {}, "x" * 100)


@pytest.mark.parametrize(
    "expected",
    [
        ["probe.user@example.com"],
        ["PROBE.USER@EXAMPLE.COM"],
        ["  probe.user@example.com  "],
        ["7"],
        ["true"],
        ["Haifa"],
        ["7", "probe.user@example.com", "Haifa"],
    ],
)
def test_expected_values_found_anywhere(expected: list[str]) -> None:
    body = (
        '{"id":7,"active":true,"email":" Probe.User@Example.com ",'
        '"address":{"city":"Haifa"},"roles":[{"name":"admin"}]}'
    )

    assert missing_values(body, expected) == ()


def test_missing_values_are_named_in_order() -> None:
    body = '{"id":7,"email":"other@example.com","tags":["a","b"]}'

    assert missing_values(body, ["probe.user@example.com", "a", "8", "admin"]) == (
        "probe.user@example.com",
        "8",
        "admin",
    )


def test_numbers_compare_as_their_text() -> None:
    assert missing_values('{"price":250.0,"rate":2.5}', ["250", "2.5"]) == ()
    assert missing_values('{"id":"0007"}', ["7"]) == ("7",)


def test_keys_are_not_values() -> None:
    assert missing_values('{"email":"x"}', ["email"]) == ("email",)


def test_a_body_that_is_not_json_lacks_every_value() -> None:
    assert missing_values("probe.user@example.com", ["probe.user@example.com"]) == (
        "probe.user@example.com",
    )
    assert missing_values("anything", []) == ()
