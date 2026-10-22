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

import re
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.csharp_source import (
    StringLiteral,
    source_files,
    string_literals,
    walk_files,
)
from hw_checker.domain import PathFixKind, PathNotRewrittenReason, PathRewrite
from hw_checker.domain.execution import MAX_PATH_TEXT_CHARS

_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/](?P<rest>.*)$", re.DOTALL)
_UNC_PATH = re.compile(r"^\\\\[^\\/\s]+[\\/][^\\/\s]+(?P<rest>.*)$", re.DOTALL)
_NAME = r"[^<>:\"|?*{}\\/\s][^<>:\"|?*{}\\/]*"
_RELATIVE_FILE = re.compile(rf"^(?:\./)?(?:{_NAME}[\\/])*{_NAME}\.[A-Za-z0-9]{{1,8}}$")
"""A relative file path with an extension, like `users.txt` or `Data\\users.txt`."""
_SEPARATORS = re.compile(r"[\\/]+")


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
    for source in source_files(working_copy):
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


def _project_files(project_root: Path) -> list[str]:
    return [path.relative_to(project_root).as_posix() for path in walk_files(project_root)]
