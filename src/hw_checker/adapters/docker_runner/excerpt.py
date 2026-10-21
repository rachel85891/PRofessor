"""Shortens a full build or run log to a bounded excerpt for the report."""

import re

from hw_checker.domain.execution import MAX_LOGS_EXCERPT_CHARS

GAP_MARKER = "[...]"

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_LINE = re.compile(r"\berror (?:CS|NU|MSB)\d+|\bUnhandled exception\b")


def excerpt_log(text: str, limit: int = MAX_LOGS_EXCERPT_CHARS) -> str:
    """Return at most `limit` characters of `text`, made of whole lines.

    ANSI color codes are removed and line endings normalized to `\\n`. A log that fits is
    kept whole. Otherwise compiler, NuGet and MSBuild error lines (and unhandled exceptions)
    come first, then `[...]`, then as much of the end of the log as fits.
    """
    clean = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    if len(clean) <= limit:
        return clean
    lines = clean.split("\n")
    budget = limit - len(GAP_MARKER) - 1
    chosen_errors: list[int] = []
    for index, line in enumerate(lines):
        if _ERROR_LINE.search(line) and len(line) + 1 <= budget:
            chosen_errors.append(index)
            budget -= len(line) + 1
    tail: list[int] = []
    for index in range(len(lines) - 1, -1, -1):
        if index in chosen_errors:
            continue
        if len(lines[index]) + 1 > budget:
            break
        tail.append(index)
        budget -= len(lines[index]) + 1
    parts = [lines[index] for index in chosen_errors]
    parts.append(GAP_MARKER)
    parts.extend(lines[index] for index in reversed(tail))
    return "\n".join(parts)
