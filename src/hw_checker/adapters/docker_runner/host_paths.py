"""Finds file paths in student C# code that cannot work in the container, and adapts them.

Three kinds of path are handled, all only in the runner's temporary copy (design §9, step 4):

- an absolute host path such as `"E:\\API\\web-api\\Repository\\Data.txt"`: rewritten to the
  one file (or folder) of the repo, in any of its projects, whose path ends with the longest
  matching trailing part, compared ignoring case; or flagged when that part matches several;
- a relative path whose letter case differs from the real file (`"users.txt"` for a committed
  `Users.txt`), or that is written with `\\` (`"..\\Repository\\users.txt"`): Windows ignores
  case and reads `\\` as a separator, and Linux does neither, so the literal is rewritten to
  the real file's path with `/`, or flagged when two files differ only in case;
- a path to a data file that is not in the repo (the student has it on their own machine
  only): an absolute path that matches nothing, or a relative one whose folder is missing too.
  It is rewritten to the place in the copy where its trailing part points (the Web project's
  folder when that is ambiguous), and an empty file is created there in the run's copy, so
  the app can start and be probed (developer decision 2026-11-11). A relative path whose
  folder exists is left alone: the app may create the file itself, as on Windows.

Every relative path is read from the Web project's folder, the app's working directory, as
`dotnet run` does. Rewritten paths are relative to that folder too. Only the exact string
literal changes; the student's working copy is never written to. The C# source is read as
text and scanned for string literals; nothing is compiled or executed.
"""

import posixpath
import re
from dataclasses import dataclass, field
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
"""A relative file path with an extension, like `users.txt` or `..\\Data\\users.txt`."""
_FILE_NAME = re.compile(rf"^{_NAME}\.[A-Za-z0-9]{{1,8}}$")
"""One file name with an extension: what an empty file may be created as."""
_SAFE_SEGMENT = re.compile(rf"^{_NAME}$")
_SEPARATORS = re.compile(r"[\\/]+")
WINDOWS_SEPARATOR = "\\"
PARENT = ".."


@dataclass(frozen=True)
class RepoLayout:
    """The repo's files and folders (repo-relative, `/`), and the Web project's folder.

    `files` and `folders` skip `bin/`, `obj/` and `.git/`; `folders` never holds the root.
    """

    files: tuple[str, ...]
    web_folder: str = ""
    folders: tuple[str, ...] = field(default=())

    @classmethod
    def of(cls, files: list[str] | tuple[str, ...], web_folder: str = "") -> "RepoLayout":
        """The layout of `files`; its folders are their parent folders."""
        folders: dict[str, None] = {}
        for path in files:
            parent = posixpath.dirname(path)
            while parent:
                folders.setdefault(parent)
                parent = posixpath.dirname(parent)
        return cls(tuple(files), web_folder, tuple(sorted(folders)))

    def from_web(self, path: str) -> str:
        """`path` (repo-relative) as seen from the Web project's folder."""
        return posixpath.relpath(path or ".", self.web_folder or ".")

    def resolve(self, relative: str) -> str | None:
        """The repo-relative path a relative path from the Web project's folder names, or
        None when it leads out of the repo."""
        joined = posixpath.normpath(posixpath.join(self.web_folder or ".", relative))
        if joined == PARENT or joined.startswith(PARENT + "/"):
            return None
        return "" if joined == "." else joined


@dataclass(frozen=True)
class PathPlan:
    """What to change in the temporary copy, and the record of every detection.

    `overrides` maps a working-copy-relative path (forward slashes) to its new bytes.
    `created` lists the repo-relative paths of the empty files to create in the run's
    writable copy, each once.
    """

    rewrites: tuple[PathRewrite, ...]
    overrides: dict[str, bytes]
    created: tuple[str, ...] = ()


def plan_path_fixes(working_copy: Path, project_folder: str) -> PathPlan:
    """Scan every `.cs` file of the working copy and plan the path rewrites.

    `project_folder` is the Web project's folder relative to the working copy ("" at the
    root); it is the app's working directory, so rewritten paths are relative to it. Paths
    are matched against the files of every project of the repo.
    """
    layout = RepoLayout.of(
        [path.relative_to(working_copy).as_posix() for path in walk_files(working_copy)],
        project_folder,
    )
    rewrites: list[PathRewrite] = []
    overrides: dict[str, bytes] = {}
    for source in source_files(working_copy):
        relative = source.relative_to(working_copy).as_posix()
        data = source.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found, new_text = fix_source(text, relative, layout)
        rewrites.extend(found)
        if new_text != text:
            overrides[relative] = new_text.encode("utf-8")
    created = dict.fromkeys(rewrite.created_path for rewrite in rewrites if rewrite.created_path)
    return PathPlan(tuple(rewrites), overrides, tuple(created))


def is_absolute_host_path(value: str) -> bool:
    """True for `C:\\x`, `C:/x` and `\\\\server\\share\\x`; never for `C:` alone or a URL."""
    match = _DRIVE_PATH.match(value) or _UNC_PATH.match(value)
    return match is not None and match["rest"].strip("\\/ ") != ""


def match_project_file(host_path: str, project_files: list[str]) -> str | PathNotRewrittenReason:
    """The single file (or folder) matched by the longest trailing part of `host_path`.

    Parts are compared ignoring case. A match on the file name alone is accepted only when
    that name is unique in the list, which the same rule gives: the longest matching
    trailing part must match exactly one entry.
    """
    return _match_tail(_segments(host_path), project_files)


def _segments(path: str) -> list[str]:
    return [part for part in _SEPARATORS.split(_strip_root(path)) if part and part != "."]


def _match_tail(
    segments: list[str], candidates: list[str] | tuple[str, ...]
) -> str | PathNotRewrittenReason:
    lowered = [(path, path.lower()) for path in candidates]
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


@dataclass(frozen=True)
class _Target:
    """Where a detected path leads in the copy (repo-relative), and whether an empty file
    is created there."""

    path: str
    created: bool = False


def fix_source(
    text: str, relative: str, layout: RepoLayout | list[str]
) -> tuple[list[PathRewrite], str]:
    """The paths detected in one source file, and the file's text with the safe rewrites.

    `relative` names the file in the records; `layout` is the repo's (a plain list is the
    files of a repo whose Web project is at the root).
    """
    if not isinstance(layout, RepoLayout):
        layout = RepoLayout.of(layout)
    found: list[PathRewrite] = []
    replacements: list[tuple[StringLiteral, str]] = []
    for literal in string_literals(text):
        value = literal.value
        if "\n" in value or not value.strip() or len(value) > MAX_PATH_TEXT_CHARS:
            continue
        if is_absolute_host_path(value):
            kind = PathFixKind.ABSOLUTE
            outcome = (
                PathNotRewrittenReason.DYNAMIC
                if literal.has_holes
                else _place_by_tail(_segments(value), layout)
            )
        elif literal.has_holes or not _RELATIVE_FILE.match(value):
            continue
        else:
            detected = _relative(value, layout)
            if detected is None:
                continue
            kind, outcome = detected
        found.append(_record(relative, literal, kind, outcome, layout))
        # The reasons are a StrEnum, so test for them, not for str.
        if not isinstance(outcome, PathNotRewrittenReason):
            replacements.append((literal, layout.from_web(outcome.path)))
    for literal, new_path in sorted(replacements, key=lambda item: -item[0].content_start):
        text = (
            text[: literal.content_start] + _escape(new_path, literal) + text[literal.content_end :]
        )
    return found, text


def _relative(
    value: str, layout: RepoLayout
) -> tuple[PathFixKind, _Target | PathNotRewrittenReason] | None:
    """What a relative path needs, or None when it works in the container as written."""
    normalized = value.replace(WINDOWS_SEPARATOR, "/")
    resolved = layout.resolve(normalized)
    if resolved is None:
        # It leads out of the repo, to a file on the student's machine: read it like an
        # absolute path, by its trailing part.
        return PathFixKind.RELATIVE, _place_by_tail(_segments(value), layout)
    if resolved in layout.files:
        return None if normalized == value else (PathFixKind.SEPARATOR, _Target(resolved))
    same_name = [path for path in layout.files if path.lower() == resolved.lower()]
    if len(same_name) == 1:
        return PathFixKind.CASE, _Target(same_name[0])
    if same_name:
        return PathFixKind.CASE, PathNotRewrittenReason.SEVERAL_MATCHES
    folder, name = posixpath.split(resolved)
    real_folder = _real_folder(folder, layout)
    if real_folder is not None:
        if normalized == value and real_folder == folder:
            return None  # the file may be created by the app, as on Windows
        kind = PathFixKind.SEPARATOR if normalized != value else PathFixKind.CASE
        return kind, _Target(posixpath.join(real_folder, name))
    return PathFixKind.RELATIVE, _created(resolved, layout)


def _real_folder(folder: str, layout: RepoLayout) -> str | None:
    """The repo's folder that `folder` names, ignoring case; None when there is none (or
    several)."""
    if folder == "" or folder in layout.folders:
        return folder
    same = [path for path in layout.folders if path.lower() == folder.lower()]
    return same[0] if len(same) == 1 else None


def _place_by_tail(segments: list[str], layout: RepoLayout) -> _Target | PathNotRewrittenReason:
    """The repo's file or folder a path's trailing part names; or, for a file name that
    matches nothing, the empty file to create where the rest of the trailing part points."""
    matched = _match_tail(segments, layout.files)
    if matched is PathNotRewrittenReason.NO_MATCH:
        matched = _match_tail(segments, layout.folders)
    if not isinstance(matched, PathNotRewrittenReason):
        return _Target(matched)
    if matched is PathNotRewrittenReason.SEVERAL_MATCHES or not segments:
        return matched
    return _created(_missing_place(segments, layout), layout)


def _missing_place(segments: list[str], layout: RepoLayout) -> str:
    """Where a missing file's path points in the repo: under the deepest of its folders that
    names a single repo folder by its trailing part, with the folders after it; in the Web
    project's folder when none does, or when that is ambiguous."""
    folders = segments[:-1]
    for end in range(len(folders), 0, -1):
        matched = _match_tail(folders[:end], layout.folders)
        if matched is PathNotRewrittenReason.SEVERAL_MATCHES:
            break
        # The reasons are a StrEnum, so test for them, not for str.
        if not isinstance(matched, PathNotRewrittenReason):
            return posixpath.join(matched, *folders[end:], segments[-1])
    return posixpath.join(layout.web_folder, segments[-1])


def _created(path: str, layout: RepoLayout) -> _Target | PathNotRewrittenReason:
    """`path` as an empty file to create, when it is safe: a file name with an extension,
    folders that are plain names, and nothing of the repo in its place."""
    parts = path.split("/")
    safe = (
        _FILE_NAME.match(parts[-1]) is not None
        and all(_SAFE_SEGMENT.match(part) and part not in (".", PARENT) for part in parts)
        and not any(path.lower() == other.lower() for other in (*layout.files, *layout.folders))
    )
    return _Target(path, created=True) if safe else PathNotRewrittenReason.NO_MATCH


def _record(
    relative: str,
    literal: StringLiteral,
    kind: PathFixKind,
    outcome: _Target | PathNotRewrittenReason,
    layout: RepoLayout,
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
        file_path=relative,
        line=literal.line,
        kind=kind,
        original=literal.value,
        rewritten=layout.from_web(outcome.path),
        created_path=outcome.path if outcome.created else None,
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
