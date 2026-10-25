import pytest

from hw_checker.adapters.docker_runner.http_response import (
    EXCERPT_CHARS,
    HttpResponse,
    excerpt,
    parse_response,
)


def test_status_headers_and_body_are_parsed() -> None:
    raw = (
        "HTTP/1.1 201 Created\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Location: http://app:8080/api/Users/3\r\n"
        "\r\n"
        '{"id":3,"userName":"newuser"}'
    )

    response = parse_response(raw)

    assert response == HttpResponse(
        201,
        {
            "content-type": "application/json; charset=utf-8",
            "location": "http://app:8080/api/Users/3",
        },
        '{"id":3,"userName":"newuser"}',
    )
    assert response is not None and response.header("LOCATION") == "http://app:8080/api/Users/3"


def test_interim_continue_responses_are_skipped() -> None:
    raw = "HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 204 No Content\r\nServer: Kestrel\r\n\r\n"

    assert parse_response(raw) == HttpResponse(204, {"server": "Kestrel"}, "")


def test_bare_newlines_and_a_missing_body_are_accepted() -> None:
    assert parse_response("HTTP/2 404\nserver: Kestrel") == HttpResponse(
        404, {"server": "Kestrel"}, ""
    )


@pytest.mark.parametrize("raw", ["", "000", "curl: (7) Failed to connect", "<html>"])
def test_output_without_a_status_line_is_no_response(raw: str) -> None:
    assert parse_response(raw) is None


def test_a_body_with_blank_lines_is_kept_whole() -> None:
    raw = "HTTP/1.1 200 OK\r\n\r\nfirst\r\n\r\nsecond"

    response = parse_response(raw)

    assert response is not None and response.body == "first\r\n\r\nsecond"


def test_excerpt_is_trimmed_and_bounded() -> None:
    assert excerpt('  {"id":3}\n') == '{"id":3}'
    long = excerpt("x" * 2000)
    assert len(long) == EXCERPT_CHARS and long.endswith("...")
