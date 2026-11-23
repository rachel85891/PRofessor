"""`send_with_urllib` checks the server's certificate with the system TLS context."""

import ssl
import urllib.error
import urllib.request
from io import BytesIO
from typing import Any

import pytest

import hw_checker.adapters.github_api.client as client_module
from hw_checker.adapters.github_api.client import send_with_urllib

URL = "https://api.github.test/repos/dana/api"
TIMEOUT = 7.0


class FakeResponse:
    """What `urlopen` returns: a context manager with a status and a body."""

    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok": true}'


def test_the_request_is_sent_with_the_system_tls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    context = ssl.create_default_context()
    monkeypatch.setattr(client_module, "system_tls_context", lambda: context)
    seen: list[dict[str, Any]] = []

    def fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> FakeResponse:
        seen.append({"url": request.full_url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    status, body = send_with_urllib(urllib.request.Request(URL), TIMEOUT)

    assert (status, body) == (200, b'{"ok": true}')
    assert len(seen) == 1
    assert seen[0]["context"] is context
    assert (seen[0]["url"], seen[0]["timeout"]) == (URL, TIMEOUT)


def test_an_http_error_is_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: urllib.request.Request, **kwargs: Any) -> FakeResponse:
        raise urllib.error.HTTPError(URL, 404, "Not Found", {}, BytesIO(b"missing"))  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    assert send_with_urllib(urllib.request.Request(URL), TIMEOUT) == (404, b"missing")
