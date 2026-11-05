"""Finds paths written with Windows folder separators in the app's file-access errors.

A student's `"..\\repository\\Users.txt"` works on Windows, but in the Linux container `\\`
is part of a file name, so opening the file fails and the request answers 500. The run log
then holds a .NET file or directory exception naming the path.
"""

import re

from hw_checker.domain.execution import MAX_PATH_TEXT_CHARS

FILE_EXCEPTIONS = ("FileNotFoundException", "DirectoryNotFoundException")
"""The .NET exceptions of a file or folder that cannot be found."""
WINDOWS_SEPARATOR = "\\"
"""The Windows folder separator; on Linux it is an ordinary character of a file name."""
MAX_WINDOWS_PATHS = 4
"""The most distinct paths kept; one is usually enough to explain the failures."""

_EXCEPTION_LINE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(name) for name in FILE_EXCEPTIONS)
    + r")\b[^\n']*'(?P<path>[^'\n]+)'"
)


def windows_paths_in_log(log: str) -> tuple[str, ...]:
    """The distinct paths with `WINDOWS_SEPARATOR` that a `FILE_EXCEPTIONS` line of `log`
    names, in order of appearance."""
    found: dict[str, None] = {}
    for match in _EXCEPTION_LINE.finditer(log):
        path = match["path"]
        if WINDOWS_SEPARATOR in path and len(path) <= MAX_PATH_TEXT_CHARS:
            found.setdefault(path)
            if len(found) == MAX_WINDOWS_PATHS:
                break
    return tuple(found)
