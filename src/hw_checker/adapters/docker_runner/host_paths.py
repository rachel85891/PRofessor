"""Finds file paths in student C# code that cannot work in the container, and adapts them.

Two kinds of path are handled, both only in the runner's temporary copy (design §9, step 4):

- an absolute host path such as `"E:\\API\\web-api\\wwwroot\\Data.txt"`: rewritten to the one
  project file whose path ends with the longest matching trailing part, compared ignoring
  case, or flagged when there is no such single file;
- a relative path whose letter case differs from the real file (`"users.txt"` for a committed
  `Users.txt`): Windows ignores case and Linux does not, so the literal is rewritten to the
  real file's name, or flagged when two files differ only in case.

Only the exact string literal changes; the student's working copy is never written to. The
C# source is read as text and scanned for string literals; nothing is compiled or executed.
"""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.discovery import EXCLUDED_DIRS
from hw_checker.domain import PathFixKind, PathNotRewrittenReason, PathRewrite
from hw_checker.domain.execution import MAX_PATH_TEXT_CHARS

MAX_SOURCE_FILE_BYTES = 1_000_000
"""Larger `.cs` files are not scanned (generated code, not student logic)."""

_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/](?P<rest>.*)$", re.DOTALL)
_UNC_PATH = re.compile(r"^\\\\[^\\/\s]+[\\/][^\\/\s]+(?P<rest>.*)$", re.DOTALL)
_NAME = r"[^<>:\"|?*{}\\/\s][^<>:\"|?*{}\\/]*"
_RELATIVE_FILE = re.compile(rf"^(?:\./)?(?:{_NAME}[\\/])*{_NAME}\.[A-Za-z0-9]{{1,8}}$")
"""A relative file path with an extension, like `users.txt` or `Data\\users.txt`."""
_SEPARATORS = re.compile(r"[\\/]+")
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


@dataclass(frozen=True)
class PathPlan:
    """What to change in the temporary copy, and the record of every detection.

    `overrides` maps a working-copy-relative path (forward slashes) to its new bytes.
    """

    rewrites: tuple[PathRewrite, ...]
    overrides: dict[str, bytes]


def plan_path_fixes(working_copy: Path, project_folder: str) -> PathPlan:
    """Scan every `.cs` file of the working copy and plan the path rewrites.

    `project_folder` is the Web project's folder relative to the working copy ("" at the
    root); it is the app's working directory, so rewritten paths are relative to it.
    """
    project_root = working_copy / project_folder
    project_files = _project_files(project_root)
    rewrites: list[PathRewrite] = []
    overrides: dict[str, bytes] = {}
    for source in _source_files(working_copy):
        relative = source.relative_to(working_copy).as_posix()
        data = source.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found, new_text = fix_source(text, relative, project_files)
        rewrites.extend(found)
        if new_text != text:
            overrides[relative] = new_text.encode("utf-8")
    return PathPlan(tuple(rewrites), overrides)


def is_absolute_host_path(value: str) -> bool:
    """True for `C:\\x`, `C:/x` and `\\\\server\\share\\x`; never for `C:` alone or a URL."""
    match = _DRIVE_PATH.match(value) or _UNC_PATH.match(value)
    return match is not None and match["rest"].strip("\\/ ") != ""


def string_literals(text: str) -> list[StringLiteral]:
    """Every string literal of a C# source, skipping comments and character literals."""
    return list(_Scanner(text).literals())


def match_project_file(host_path: str, project_files: list[str]) -> str | PathNotRewrittenReason:
    """The single project file matched by the longest trailing part of `host_path`.

    Parts are compared ignoring case. A match on the file name alone is accepted only when
    that name is unique in the project, which the same rule gives: the longest matching
    trailing part must match exactly one file.
    """
    segments = [part for part in _SEPARATORS.split(_strip_root(host_path)) if part]
    lowered = [(path, path.lower()) for path in project_files]
    for length in range(len(segments), 0, -1):
        suffix = "/".join(segments[-length:]).lower()
        matches = [path for path, low in lowered if low == suffix or low.endswith("/" + suffix)]
        if len(matches) == 1:
            return matches[0]
        if matches:
            return PathNotRewrittenReason.SEVERAL_MATCHES
    return PathNotRewrittenReason.NO_MATCH


def _strip_root(host_path: str) -> str:
    unc = _UNC_PATH.match(host_path)
    if unc is not None:
        return unc["rest"]
    drive = _DRIVE_PATH.match(host_path)
    return drive["rest"] if drive is not None else host_path


def fix_source(text: str, relative: str, project_files: list[str]) -> tuple[list[PathRewrite], str]:
    """The paths detected in one source file, and the file's text with the safe rewrites.

    `relative` names the file in the records; `project_files` are the project folder's files.
    """
    exact = set(project_files)
    by_lower: dict[str, list[str]] = {}
    for path in project_files:
        by_lower.setdefault(path.lower(), []).append(path)
    found: list[PathRewrite] = []
    replacements: list[tuple[StringLiteral, str]] = []
    for literal in string_literals(text):
        value = literal.value
        if "\n" in value or not value.strip() or len(value) > MAX_PATH_TEXT_CHARS:
            continue
        if is_absolute_host_path(value):
            if literal.has_holes:
                outcome: str | PathNotRewrittenReason = PathNotRewrittenReason.DYNAMIC
            else:
                outcome = match_project_file(value, project_files)
            found.append(_record(relative, literal, PathFixKind.ABSOLUTE, outcome))
            # The reasons are a StrEnum, so test for them, not for str.
            if not isinstance(outcome, PathNotRewrittenReason):
                replacements.append((literal, outcome))
            continue
        if literal.has_holes or not _RELATIVE_FILE.match(value):
            continue
        normalized = value.replace("\\", "/").removeprefix("./")
        if normalized in exact:
            continue
        same_name = by_lower.get(normalized.lower(), [])
        if len(same_name) == 1:
            found.append(_record(relative, literal, PathFixKind.CASE, same_name[0]))
            replacements.append((literal, same_name[0]))
        elif same_name:
            reason = PathNotRewrittenReason.SEVERAL_MATCHES
            found.append(_record(relative, literal, PathFixKind.CASE, reason))
    for literal, new_path in sorted(replacements, key=lambda item: -item[0].content_start):
        text = (
            text[: literal.content_start] + _escape(new_path, literal) + text[literal.content_end :]
        )
    return found, text


def _record(
    relative: str, literal: StringLiteral, kind: PathFixKind, outcome: str | PathNotRewrittenReason
) -> PathRewrite:
    if isinstance(outcome, PathNotRewrittenReason):
        return PathRewrite(
            file_path=relative,
            line=literal.line,
            kind=kind,
            original=literal.value,
            not_rewritten_reason=outcome,
        )
    return PathRewrite(
        file_path=relative, line=literal.line, kind=kind, original=literal.value, rewritten=outcome
    )


def _escape(path: str, literal: StringLiteral) -> str:
    """Write `path` (forward slashes) as the content of a literal of the same kind."""
    if literal.interpolated:
        path = path.replace("{", "{{").replace("}", "}}")
    if literal.raw:
        return path
    if literal.verbatim:
        return path.replace('"', '""')
    return path.replace("\\", "\\\\").replace('"', '\\"')


def _walk(root: Path) -> Iterator[Path]:
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


def _source_files(working_copy: Path) -> list[Path]:
    return [
        path
        for path in _walk(working_copy)
        if path.suffix.lower() == ".cs" and path.stat().st_size <= MAX_SOURCE_FILE_BYTES
    ]


def _project_files(project_root: Path) -> list[str]:
    return [path.relative_to(project_root).as_posix() for path in _walk(project_root)]


class _Scanner:
    """A small C# lexer that only understands comments, characters and strings."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def literals(self) -> Iterator[StringLiteral]:
        text = self.text
        while self.position < len(text):
            character = text[self.position]
            if text.startswith("//", self.position):
                end = text.find("\n", self.position)
                self.position = len(text) if end < 0 else end
            elif text.startswith("/*", self.position):
                end = text.find("*/", self.position + 2)
                self.position = len(text) if end < 0 else end + 2
            elif character == "'":
                self._skip_char_literal()
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
