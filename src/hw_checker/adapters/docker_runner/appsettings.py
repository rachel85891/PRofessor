"""Reads the connection names an `appsettings*.json` file declares, as .NET reads it.

.NET's JSON configuration provider skips comments and allows trailing commas, which
Python's `json` rejects, so both are removed (outside strings) before parsing. Only the
names under `ConnectionStrings` are read, never their values: a value may hold a password.
"""

import json
from typing import Any

CONNECTION_STRINGS_SECTION = "ConnectionStrings"
SETTINGS_PREFIX = "appsettings"
BOM = chr(0xFEFF)


def is_settings_file(file_name: str) -> bool:
    """True for `appsettings.json` and `appsettings.<Environment>.json`, ignoring case."""
    lowered = file_name.lower()
    return lowered.startswith(SETTINGS_PREFIX) and lowered.endswith(".json")


def connection_names(text: str) -> list[str] | None:
    """The names under `ConnectionStrings` of one settings file, or None when the file is not
    JSON that .NET would read.

    A name whose value is a section (an object or a list) is not a connection and is left
    out. The section is matched ignoring case, as configuration keys are.
    """
    try:
        document: Any = json.loads(_drop_trailing_commas(_drop_comments(text.lstrip(BOM))))
    except ValueError:
        return None
    if not isinstance(document, dict):
        return []
    names: list[str] = []
    for key, section in document.items():
        if key.lower() == CONNECTION_STRINGS_SECTION.lower() and isinstance(section, dict):
            names += [name for name, value in section.items() if not isinstance(value, dict | list)]
    return names


def _drop_comments(text: str) -> str:
    """`text` without `//` and `/* */` comments, which lie outside strings."""
    kept: list[str] = []
    index, in_string = 0, False
    while index < len(text):
        character = text[index]
        if in_string:
            kept.append(character)
            if character == "\\" and index + 1 < len(text):
                kept.append(text[index + 1])
                index += 1
            elif character == '"':
                in_string = False
            index += 1
        elif character == '"':
            in_string = True
            kept.append(character)
            index += 1
        elif text.startswith("//", index):
            end = text.find("\n", index)
            index = len(text) if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            kept.append(" ")
        else:
            kept.append(character)
            index += 1
    return "".join(kept)


def _drop_trailing_commas(text: str) -> str:
    """`text` without a comma that is followed only by whitespace and `}` or `]`."""
    kept: list[str] = []
    index, in_string = 0, False
    while index < len(text):
        character = text[index]
        if in_string:
            kept.append(character)
            if character == "\\" and index + 1 < len(text):
                kept.append(text[index + 1])
                index += 1
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            kept.append(character)
        elif character == "," and _closes_next(text, index + 1):
            pass
        else:
            kept.append(character)
        index += 1
    return "".join(kept)


def _closes_next(text: str, index: int) -> bool:
    while index < len(text) and text[index].isspace():
        index += 1
    return index < len(text) and text[index] in "}]"
