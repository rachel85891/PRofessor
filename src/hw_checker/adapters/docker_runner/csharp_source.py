"""Reads C# source as text: string literals, and a copy with comments and strings blanked.

A small lexer that only understands comments, character literals and the string literal
forms (regular, verbatim, interpolated, raw). Student code is never compiled or executed.
Used by the host-path rewriting and by the route table extraction.
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.discovery import EXCLUDED_DIRS

MAX_SOURCE_FILE_BYTES = 1_000_000
"""Larger `.cs` files are not scanned (generated code, not student logic)."""

_SIMPLE_ESCAPES = {
    "\\": "\\",
    '"': '"',
    "'": "'",
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}


@dataclass(frozen=True)
class StringLiteral:
    """One C# string literal: where its text sits in the source, and what it says.

    `content_start:content_end` is the text between the quotes. `value` is that text decoded
    (escapes resolved); for an interpolated string with holes it is the text before the
    first hole, and `has_holes` is set.
    """

    content_start: int
    content_end: int
    value: str
    line: int
    verbatim: bool
    interpolated: bool
    raw: bool
    has_holes: bool


def string_literals(text: str) -> list[StringLiteral]:
    """Every string literal of a C# source, skipping comments and character literals."""
    return list(_Scanner(text).literals())


@dataclass(frozen=True)
class MaskedSource:
    """A C# source with comments, character literals and string contents blanked out.

    `text` has the same length and line breaks as the source, so positions and line numbers
    carry over; code structure (brackets, braces, identifiers) can be searched without
    matching anything inside a comment or a string. `literals` are the string literals.
    """

    text: str
    literals: tuple[StringLiteral, ...]

    def literal_in(self, start: int, end: int) -> list[StringLiteral]:
        """The literals whose content lies between `start` and `end`."""
        return [lit for lit in self.literals if start <= lit.content_start < end]

    def line_of(self, position: int) -> int:
        return self.text.count("\n", 0, position) + 1


def mask_source(text: str) -> MaskedSource:
    """Blank comments, character literals and string contents, keeping line breaks."""
    scanner = _Scanner(text)
    literals = tuple(scanner.literals())
    characters = list(text)
    spans = [*scanner.blanked, *((lit.content_start, lit.content_end) for lit in literals)]
    for start, end in spans:
        for index in range(start, min(end, len(characters))):
            if characters[index] != "\n":
                characters[index] = " "
    return MaskedSource("".join(characters), literals)


def walk_files(root: Path) -> Iterator[Path]:
    """Every file under `root`, skipping `bin/`, `obj/`, `.git/` and symlinks."""
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name.lower() not in EXCLUDED_DIRS and not Path(directory, name).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory, name)
            if not path.is_symlink():
                yield path


def source_files(root: Path) -> list[Path]:
    """The `.cs` files under `root` that are small enough to scan."""
    return [
        path
        for path in walk_files(root)
        if path.suffix.lower() == ".cs" and path.stat().st_size <= MAX_SOURCE_FILE_BYTES
    ]


class _Scanner:
    """A small C# lexer that only understands comments, characters and strings."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0
        self.blanked: list[tuple[int, int]] = []
        """Spans of comments and character literals, for `mask_source`."""

    def literals(self) -> Iterator[StringLiteral]:
        text = self.text
        while self.position < len(text):
            character = text[self.position]
            if text.startswith("//", self.position):
                end = text.find("\n", self.position)
                end = len(text) if end < 0 else end
                self.blanked.append((self.position, end))
                self.position = end
            elif text.startswith("/*", self.position):
                end = text.find("*/", self.position + 2)
                end = len(text) if end < 0 else end + 2
                self.blanked.append((self.position, end))
                self.position = end
            elif character == "'":
                start = self.position
                self._skip_char_literal()
                self.blanked.append((start, self.position))
            elif character in '"@$':
                literal = self._string_literal()
                if literal is not None:
                    yield literal
            else:
                self.position += 1

    def _skip_char_literal(self) -> None:
        index = self.position + 1
        while index < len(self.text) and self.text[index] not in "'\n":
            index += 2 if self.text[index] == "\\" else 1
        self.position = index + 1

    def _string_literal(self) -> StringLiteral | None:
        text = self.text
        start = self.position
        index = start
        dollars = 0
        verbatim = False
        while index < len(text) and text[index] in "@$":
            if text[index] == "$":
                dollars += 1
            else:
                verbatim = True
            index += 1
        if index >= len(text) or text[index] != '"':
            self.position = start + 1  # an identifier like @class, or a lone $
            return None
        line = text.count("\n", 0, start) + 1
        rest = text[index:]
        quotes = len(rest) - len(rest.lstrip('"'))
        if quotes >= 3:
            return self._raw(index, quotes, line, dollars)
        if verbatim:
            return self._verbatim(index, line, dollars > 0)
        return self._regular(index, line, dollars > 0)

    def _regular(self, quote: int, line: int, interpolated: bool) -> StringLiteral:
        text = self.text
        index = quote + 1
        decoded: list[str] = []
        holes = False
        cut: int | None = None
        while index < len(text) and text[index] not in '"\n':
            character = text[index]
            if character == "\\" and index + 1 < len(text):
                decoded.append(_SIMPLE_ESCAPES.get(text[index + 1], text[index + 1]))
                index += 2
            elif interpolated and character == "{":
                if text.startswith("{{", index):
                    decoded.append("{")
                    index += 2
                else:
                    holes = True
                    cut = len(decoded) if cut is None else cut
                    index = self._skip_hole(index, multiline=False)
            elif interpolated and text.startswith("}}", index):
                decoded.append("}")
                index += 2
            else:
                decoded.append(character)
                index += 1
        decoded = decoded if cut is None else decoded[:cut]
        return self._finish(quote + 1, index, decoded, line, False, interpolated, False, holes)

    def _verbatim(self, quote: int, line: int, interpolated: bool) -> StringLiteral:
        text = self.text
        index = quote + 1
        decoded: list[str] = []
        holes = False
        cut: int | None = None
        while index < len(text):
            character = text[index]
            if character == '"':
                if text.startswith('""', index):
                    decoded.append('"')
                    index += 2
                    continue
                break
            if interpolated and character == "{":
                if text.startswith("{{", index):
                    decoded.append("{")
                    index += 2
                    continue
                holes = True
                cut = len(decoded) if cut is None else cut
                index = self._skip_hole(index, multiline=True)
                continue
            if interpolated and text.startswith("}}", index):
                decoded.append("}")
                index += 2
                continue
            decoded.append(character)
            index += 1
        decoded = decoded if cut is None else decoded[:cut]
        return self._finish(quote + 1, index, decoded, line, True, interpolated, False, holes)

    def _raw(self, quote: int, quotes: int, line: int, dollars: int) -> StringLiteral:
        text = self.text
        closing = text.find('"' * quotes, quote + quotes)
        end = len(text) if closing < 0 else closing
        content = text[quote + quotes : end]
        holes = dollars > 0 and "{" * dollars in content
        literal = StringLiteral(
            content_start=quote + quotes,
            content_end=end,
            value=content.split("{" * dollars)[0] if holes else content,
            line=line,
            verbatim=False,
            interpolated=dollars > 0,
            raw=True,
            has_holes=holes,
        )
        self.position = end + quotes
        return literal

    def _skip_hole(self, index: int, *, multiline: bool) -> int:
        """Skip an interpolation hole `{...}`, including nested braces and strings."""
        text = self.text
        depth = 0
        while index < len(text):
            character = text[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return index + 1
            elif character in '"@$':
                saved = self.position
                self.position = index
                literal = self._string_literal()
                index = self.position if literal is not None else index + 1
                self.position = saved
                continue
            elif character == "\n" and not multiline:
                return index
            index += 1
        return index

    def _finish(
        self,
        content_start: int,
        index: int,
        decoded: list[str],
        line: int,
        verbatim: bool,
        interpolated: bool,
        raw: bool,
        holes: bool,
    ) -> StringLiteral:
        self.position = index + 1
        return StringLiteral(
            content_start=content_start,
            content_end=index,
            value="".join(decoded),
            line=line,
            verbatim=verbatim,
            interpolated=interpolated,
            raw=raw,
            has_holes=holes,
        )
