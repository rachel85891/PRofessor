"""Parses the raw HTTP response the probe sidecar prints: headers first, then the body.

The sidecar runs `curl --dump-header - --output -` and cuts its output at a size limit, so
a large or binary body never fails a probe; this module turns that text back into a
status, headers and body.
"""

import re
from dataclasses import dataclass, field

EXCERPT_CHARS = 500
"""The most of a response body kept for the reviewer."""

_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(?P<status>\d{3})")
_BLANK_LINE = re.compile(r"\r?\n\r?\n")


@dataclass(frozen=True)
class HttpResponse:
    """One response: the status, the header values by lower-case name, and the body text."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def header(self, name: str) -> str | None:
        """The value of header `name`, ignoring case; None when it is absent."""
        return self.headers.get(name.lower())


def parse_response(raw: str) -> HttpResponse | None:
    """The final response in `raw`, skipping interim 1xx responses; None when there is none."""
    text = raw
    while True:
        match = _STATUS_LINE.match(text)
        if match is None:
            return None
        parts = _BLANK_LINE.split(text, maxsplit=1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        status = int(match["status"])
        if 100 <= status < 200 and _STATUS_LINE.match(rest):
            text = rest
            continue
        return HttpResponse(status, _headers(head), rest)


def excerpt(body: str, limit: int = EXCERPT_CHARS) -> str:
    """The start of a response body, without surrounding whitespace, at most `limit` long."""
    trimmed = body.strip()
    return trimmed if len(trimmed) <= limit else trimmed[: limit - 3] + "..."


def _headers(head: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in head.splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip():
            headers[name.strip().lower()] = value.strip()
    return headers
