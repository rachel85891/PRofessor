"""Lexical C# method signals: the methods declared directly in a type's body.

Read from masked source (comments and strings blanked) the way `csharp_signals` reads
constructors: a type's body is split into its top-level members, and a member is a method only
when its header is attributes, modifiers, a return type, a name, balanced parameters and then
nothing, a `where` constraint list or `=>`. Anything else (a constructor, an operator, a
property, a field, a nested type, a call) gives no signal, and a header whose parentheses cannot
be matched gives an `ambiguous_syntax` issue instead of a guess.
"""

import re
from typing import Final, NamedTuple

from hw_checker.domain import (
    ArchitectureSignal,
    ArchitectureSignalKind,
    DeclaringKind,
    EvidenceCoverageIssue,
    EvidenceIssueKind,
)

MAX_IDENTIFIER_CHARS: Final = 256
_NAME = r"[A-Za-z_]\w*"
_ANGLES = r"<[^<>;{}]*(?:<[^<>;{}]*(?:<[^<>;{}]*>[^<>;{}]*)*>[^<>;{}]*)*>"
_TYPE = (
    rf"(?:global\s*::\s*)?(?:\([^()]*\)|{_NAME}(?:\s*\.\s*{_NAME})*(?:\s*{_ANGLES})?)"
    r"(?:\s*\?)?(?:\s*\[[,\s]*\])*(?:\s*\?)?"
)
_MODIFIER_WORDS: Final = (
    "public",
    "private",
    "protected",
    "internal",
    "static",
    "async",
    "override",
    "virtual",
    "abstract",
    "sealed",
    "new",
    "partial",
    "extern",
    "unsafe",
    "readonly",
)
_ACCESSIBILITY: Final = frozenset({"public", "private", "protected", "internal"})
_METHOD = re.compile(
    rf"(?P<modifiers>(?:(?:{'|'.join(_MODIFIER_WORDS)})\s+)*)"
    rf"(?P<return>(?:ref\s+(?:readonly\s+)?)?{_TYPE})\s+"
    rf"(?:(?P<explicit>{_NAME}(?:\s*\.\s*{_NAME})*(?:\s*{_ANGLES})?)\s*\.\s*)?"
    rf"(?P<name>{_NAME})\s*(?:{_ANGLES})?\s*\("
)
_RESERVED: Final = frozenset(
    {
        *_MODIFIER_WORDS,
        "class",
        "struct",
        "record",
        "interface",
        "enum",
        "delegate",
        "event",
        "implicit",
        "explicit",
        "operator",
        "namespace",
        "return",
        "throw",
        "await",
        "yield",
        "goto",
        "case",
        "default",
        "if",
        "else",
        "while",
        "do",
        "for",
        "foreach",
        "switch",
        "using",
        "lock",
        "catch",
        "try",
        "finally",
        "fixed",
        "checked",
        "unchecked",
        "typeof",
        "nameof",
        "base",
        "this",
    }
)
"""Words that start other C# constructs: never a method's return type or name."""
_WHERE = re.compile(r"where\b")
_SPACE = re.compile(r"\s+")


class TypeBody(NamedTuple):
    """A type's body: the indexes of its braces, its name, and what declares it."""

    opening: int
    closing: int
    name: str
    declared_in: DeclaringKind


def matching_close(source: str, opening: int) -> int | None:
    """The index closing the `{` or `(` at `opening`."""
    open_character = source[opening]
    close_character = "}" if open_character == "{" else ")"
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == open_character:
            depth += 1
        elif source[index] == close_character:
            depth -= 1
            if depth == 0:
                return index
    return None


def top_level_member_headers(body: str) -> tuple[tuple[str, int], ...]:
    """Each member's header (the text before its `{` or `;`) and its offset in `body`."""
    headers: list[tuple[str, int]] = []
    depth = 0
    start = 0
    for index, character in enumerate(body):
        if character == "{" and depth == 0:
            headers.append((body[start:index], start))
            depth = 1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
        elif character == ";" and depth == 0:
            headers.append((body[start:index], start))
            start = index + 1
    return tuple(headers)


def method_signals(
    masked: str, file_path: str, bodies: list[TypeBody]
) -> tuple[list[ArchitectureSignal], list[EvidenceCoverageIssue]]:
    """A `method_declaration` per method declared directly in a body of `bodies`."""
    signals: list[ArchitectureSignal] = []
    issues: list[EvidenceCoverageIssue] = []
    for body in bodies:
        text = masked[body.opening + 1 : body.closing]
        for header, offset in top_level_member_headers(text):
            position = body.opening + 1 + offset
            parsed = _parse(header)
            if parsed is None:
                continue
            line = masked.count("\n", 0, position + parsed.name_start) + 1
            signal = None if parsed.unreadable else _signal(parsed, body, file_path, line)
            if signal is None:
                issues.append(
                    EvidenceCoverageIssue(
                        kind=EvidenceIssueKind.AMBIGUOUS_SYNTAX, file_path=file_path, line=line
                    )
                )
            else:
                signals.append(signal)
    return signals, issues


class _Parsed(NamedTuple):
    name: str
    name_start: int
    modifiers: tuple[str, ...]
    return_type: str
    explicit: bool
    unreadable: bool


def _parse(header: str) -> _Parsed | None:
    """The method a member header declares; None when it is not a method."""
    start = _after_attributes(header)
    if start is None:
        return None
    match = _METHOD.match(header, start)
    if match is None:
        return None
    first_word = re.match(r"(?:ref\s+(?:readonly\s+)?)?(\w+)", match["return"])
    if match["name"] in _RESERVED or (first_word is not None and first_word[1] in _RESERVED):
        return None
    parts = _Parsed(
        match["name"],
        match.start("name"),
        tuple(match["modifiers"].split()),
        _SPACE.sub(" ", match["return"]).strip(),
        match["explicit"] is not None,
        unreadable=False,
    )
    closing = matching_close(header, match.end() - 1)
    if closing is None:
        return parts._replace(unreadable=True)
    rest = header[closing + 1 :].strip()
    if rest and not rest.startswith("=>") and not _WHERE.match(rest):
        return None
    return parts


def _after_attributes(header: str) -> int | None:
    """The index after the header's leading `[...]` sections; None when one is unbalanced."""
    index = 0
    while True:
        while index < len(header) and header[index].isspace():
            index += 1
        if index >= len(header) or header[index] != "[":
            return index
        depth = 0
        for position in range(index, len(header)):
            if header[position] == "[":
                depth += 1
            elif header[position] == "]":
                depth -= 1
                if depth == 0:
                    index = position + 1
                    break
        else:
            return None


def _signal(
    parsed: _Parsed, body: TypeBody, file_path: str, line: int
) -> ArchitectureSignal | None:
    """The signal, or None when an identifier is too long to carry."""
    keywords = parsed.modifiers
    if not _ACCESSIBILITY & set(keywords) and not parsed.explicit:
        implied = "public" if body.declared_in is DeclaringKind.INTERFACE else "private"
        keywords = (implied, *keywords)
    texts = (parsed.name, body.name, parsed.return_type, *keywords)
    if any(len(text) > MAX_IDENTIFIER_CHARS for text in texts):
        return None
    return ArchitectureSignal(
        kind=ArchitectureSignalKind.METHOD_DECLARATION,
        file_path=file_path,
        line=line,
        subject=parsed.name,
        related_subject=body.name,
        keywords=keywords,
        return_type=parsed.return_type,
        declared_in=body.declared_in,
    )
