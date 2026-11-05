"""Reads C# and JavaScript source as text: string literals and the code around them.

This is a careful lexer, not a parser. It knows comments (`//`, `/* */`), quoted strings
(`"…"`, `'…'`, with backslash escapes), C# verbatim, interpolated and raw strings
(`@"…"`, `$"…"`, `$@"…"`, three or more quotes), JavaScript template literals
(`` `…${expr}…` ``) and JavaScript regex literals (`/…/flags`, told apart from division by
the token before them). Its limits:

- String literals inside a template's `${…}` and inside a C# interpolation's `{…}` are not
  recorded; the whole literal is masked.
- A regex literal after `)` or `]` is read as division (as it almost always is).
- A file that does not decode as UTF-8 is read with replacement characters.
"""

import bisect
import re
from dataclasses import dataclass, field

_REGEX_BEFORE = frozenset("(,=:[!&|?{};+-*%<>~^")
_REGEX_AFTER_WORDS = frozenset(
    {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw"}
    | {"yield", "await"}
)
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_STRING_PREFIX = re.compile(r"(\$*@?\$*)\"")


@dataclass(frozen=True)
class Literal:
    """One string literal: where it is and what it holds.

    `start` and `end` are offsets of the whole literal, delimiters included. `parts` is its
    text split at interpolations: fixed text as `str`, each `${…}` (or C# `{…}`) as None.
    `text` is the raw content between the delimiters, escapes as written.
    """

    start: int
    end: int
    text: str
    parts: tuple[str | None, ...]

    @property
    def fixed(self) -> bool:
        """True when the literal has no interpolation."""
        return None not in self.parts


@dataclass(frozen=True)
class SourceScan:
    """A source text with comments and string contents masked by spaces, and its literals.

    `masked` has the same length as the text and keeps every newline, so offsets and line
    numbers stay valid; string delimiters stay in place, so code patterns can be matched
    on it without strings or comments getting in the way.
    """

    text: str
    masked: str
    literals: tuple[Literal, ...]
    _line_starts: tuple[int, ...] = field(repr=False)

    def line_of(self, offset: int) -> int:
        """The 1-based line number of `offset`."""
        return bisect.bisect_right(self._line_starts, offset)

    def literal_at(self, offset: int) -> Literal | None:
        """The literal that starts at `offset`, if any."""
        starts = [literal.start for literal in self.literals]
        index = bisect.bisect_left(starts, offset)
        if index < len(starts) and starts[index] == offset:
            return self.literals[index]
        return None


def scan_source(text: str) -> SourceScan:
    """Scan C# or JavaScript `text` into masked code and string literals."""
    return _Scanner(text).run()


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.masked = list(text)
        self.literals: list[Literal] = []
        self.previous = ""

    def run(self) -> SourceScan:
        index = 0
        while index < len(self.text):
            index = self._step(index)
        line_starts = (0, *(match.end() for match in re.finditer("\n", self.text)))
        return SourceScan(self.text, "".join(self.masked), tuple(self.literals), line_starts)

    def _step(self, index: int) -> int:
        text = self.text
        char = text[index]
        two = text[index : index + 2]
        if two == "//":
            return self._mask_until(index, text.find("\n", index))
        if two == "/*":
            end = text.find("*/", index + 2)
            return self._mask_until(index, -1 if end < 0 else end + 2)
        if text.startswith('"""', index):
            return self._raw_string(index, index)
        if char in "\"'":
            return self._quoted(index, index, verbatim=False)
        if char in "@$" and (prefix := _string_prefix(text, index)) is not None:
            quote, verbatim = prefix
            if text.startswith('"""', quote):
                return self._raw_string(index, quote)
            return self._quoted(index, quote, verbatim=verbatim)
        if char == "`":
            return self._template(index)
        if char == "/" and self._regex_allowed():
            regex_end = _regex_end(text, index)
            if regex_end is not None:
                self._mask(index + 1, regex_end)
                self.previous = "/"
                return regex_end
        if word := _WORD.match(text, index):
            self.previous = word.group()
            return word.end()
        if not char.isspace():
            self.previous = char
        return index + 1

    def _regex_allowed(self) -> bool:
        return (
            not self.previous
            or self.previous in _REGEX_BEFORE
            or self.previous in _REGEX_AFTER_WORDS
        )

    def _quoted(self, start: int, quote_at: int, verbatim: bool) -> int:
        text = self.text
        quote = text[quote_at]
        interpolated = "$" in text[start:quote_at]
        index = quote_at + 1
        while index < len(text):
            char = text[index]
            if verbatim and char == quote and text[index + 1 : index + 2] == quote:
                index += 2
                continue
            if not verbatim and char == "\\":
                index += 2
                continue
            if char == quote or (char == "\n" and not verbatim):
                break
            index += 1
        end = min(index + 1, len(text))
        content = text[quote_at + 1 : index]
        parts = _csharp_parts(content) if interpolated else (content,)
        self._record(start, end, content, parts, quote_at + 1, index)
        return end

    def _raw_string(self, start: int, quote_at: int) -> int:
        text = self.text
        count = len(text[quote_at:]) - len(text[quote_at:].lstrip('"'))
        closing = text.find('"' * count, quote_at + count)
        content_end = len(text) if closing < 0 else closing
        end = len(text) if closing < 0 else closing + count
        content = text[quote_at + count : content_end]
        self._record(start, end, content, (content,), quote_at + count, content_end)
        return end

    def _template(self, start: int) -> int:
        text = self.text
        parts: list[str | None] = []
        piece_start = index = start + 1
        while index < len(text) and text[index] != "`":
            if text[index] == "\\":
                index += 2
            elif text.startswith("${", index):
                parts.append(text[piece_start:index])
                parts.append(None)
                index = _expression_end(text, index + 2)
                piece_start = index
            else:
                index += 1
        content_end = min(index, len(text))
        parts.append(text[piece_start:content_end])
        end = min(index + 1, len(text))
        content = text[start + 1 : content_end]
        kept = tuple(part for part in parts if part != "")
        self._record(start, end, content, kept, start + 1, content_end)
        return end

    def _record(
        self,
        start: int,
        end: int,
        content: str,
        parts: tuple[str | None, ...],
        mask_from: int,
        mask_to: int,
    ) -> None:
        self.literals.append(Literal(start, end, content, parts or ("",)))
        self._mask(mask_from, mask_to)
        self.previous = "literal"

    def _mask_until(self, start: int, end: int) -> int:
        stop = len(self.text) if end < 0 else end
        self._mask(start, stop)
        return stop

    def _mask(self, start: int, end: int) -> None:
        for position in range(start, min(end, len(self.text))):
            if self.masked[position] != "\n":
                self.masked[position] = " "


def _string_prefix(text: str, index: int) -> tuple[int, bool] | None:
    """For a C# `@"`, `$"`, `$@"` or `@$"` (or `$$"` raw prefix) at `index`, the quote's
    offset and whether the string is verbatim."""
    match = _STRING_PREFIX.match(text, index)
    if match is None or not match.group(1):
        return None
    return match.end() - 1, "@" in match.group(1)


def _csharp_parts(content: str) -> tuple[str | None, ...]:
    """Split a C# interpolated string at `{…}` holes (`{{` and `}}` are literal braces)."""
    parts: list[str | None] = []
    piece = ""
    index = 0
    while index < len(content):
        two = content[index : index + 2]
        if two in ("{{", "}}"):
            piece += two[0]
            index += 2
        elif content[index] == "{":
            parts += [piece, None] if piece else [None]
            piece = ""
            close = content.find("}", index)
            index = len(content) if close < 0 else close + 1
        else:
            piece += content[index]
            index += 1
    if piece:
        parts.append(piece)
    return tuple(parts)


def _expression_end(text: str, index: int) -> int:
    """The offset after the `}` closing a template `${` whose expression starts at `index`."""
    depth = 0
    while index < len(text):
        char = text[index]
        if char in "\"'":
            index = _quoted_end(text, index)
            continue
        if char == "`":
            index = _nested_template_end(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return index + 1
            depth -= 1
        index += 1
    return index


def _quoted_end(text: str, index: int) -> int:
    quote = text[index]
    index += 1
    while index < len(text) and text[index] not in (quote, "\n"):
        index += 2 if text[index] == "\\" else 1
    return index + 1


def _nested_template_end(text: str, index: int) -> int:
    index += 1
    while index < len(text) and text[index] != "`":
        if text[index] == "\\":
            index += 2
        elif text.startswith("${", index):
            index = _expression_end(text, index + 2)
        else:
            index += 1
    return index + 1


def _regex_end(text: str, index: int) -> int | None:
    """The offset after a regex literal starting at `index` (flags included); None when the
    slash does not start one on this line."""
    in_class = False
    position = index + 1
    while position < len(text):
        char = text[position]
        if char == "\n":
            return None
        if char == "\\":
            position += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            if position == index + 1:
                return None
            end = position + 1
            while end < len(text) and text[end].isalpha():
                end += 1
            return end
        position += 1
    return None
