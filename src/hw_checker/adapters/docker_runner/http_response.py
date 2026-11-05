"""Parses the raw HTTP response the probe sidecar prints: headers first, then the body.

The sidecar runs `curl --dump-header - --output -` and cuts its output at a size limit, so
a large or binary body never fails a probe; this module turns that text back into a
status, headers and body.
"""

import re
from dataclasses import dataclass, field

from hw_checker.domain.execution import MAX_RESPONSE_EXCERPT_CHARS

EXCERPT_CHARS = MAX_RESPONSE_EXCERPT_CHARS
"""The most of a response body kept for the reviewer."""
CUT_MARKER = " [...]"
"""Ends an excerpt that was cut."""
CLEAN_CUT_SHARE = 0.8
"""A cut moves back to the last line break or space only within this last share of the
limit; with none there, it cuts at the limit."""

_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(?P<status>\d{3})")
_BLANK_LINE = re.compile(r"\r?\n\r?\n")


@dataclass(frozen=True)
class HttpResponse:
    """One response: the status, the header values by lower-case name, and the body text.

    `truncated` is True when the sidecar's output reached the size limit, so the body may
    be cut short.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    truncated: bool = False

    def header(self, name: str) -> str | None:
        """The value of header `name`, ignoring case; None when it is absent."""
        return self.headers.get(name.lower())


def parse_response(raw: str, limit: int | None = None) -> HttpResponse | None:
    """The final response in `raw`, skipping interim 1xx responses; None when there is none.

    With `limit` (the sidecar's byte limit), a response whose raw output reached it is
    marked `truncated`.
    """
    truncated = limit is not None and len(raw.encode("utf-8")) >= limit
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
        return HttpResponse(status, _headers(head), rest, truncated)


def excerpt(body: str, limit: int = EXCERPT_CHARS) -> str:
    """The start of a response body, without surrounding whitespace, at most `limit` long.

    A longer body is cut cleanly: after the last line break or space near the limit (never
    inside a word when one is close), and marked with `CUT_MARKER`.
    """
    trimmed = body.strip()
    if len(trimmed) <= limit:
        return trimmed
    room = limit - len(CUT_MARKER)
    head = trimmed[:room]
    boundary = max(head.rfind("\n"), head.rfind(" "))
    if boundary >= room * CLEAN_CUT_SHARE:
        head = head[:boundary]
    return head.rstrip() + CUT_MARKER


def _headers(head: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in head.splitlines()[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip():
            headers[name.strip().lower()] = value.strip()
    return headers
