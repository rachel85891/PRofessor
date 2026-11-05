"""Repository checks: `.gitignore`, committed build or IDE files, and full disk paths.

They read the checked commit as the fetch step leaves it: git's tracked files (from the
index) and the working tree's files that git tracks, never anything made during the run.
Each finding lists up to `MAX_EXAMPLES` examples and the total.
"""

import re
from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import (
    ABSOLUTE_PATHS,
    GITIGNORE_MISSING,
    GITIGNORE_NOT_COVERING,
    LOCATED_EXAMPLE,
    NOT_COMMITTED,
    Localized,
    MessageTemplate,
)
from hw_checker.checks.deterministic.source_text import Literal, SourceScan, scan_source
from hw_checker.checks.deterministic.structure import repo_tree
from hw_checker.checks.registry import check
from hw_checker.domain import CheckInput, Evidence, Finding, NotEvaluatedReason, TrackedFiles

MAX_EXAMPLES = 5
MAX_SOURCE_BYTES = 1_000_000
"""Larger files (bundled libraries) are not read."""
GITIGNORE = ".gitignore"
PROJECT_SUFFIXES = (".sln", ".slnx", ".csproj")
MASK = "…"
_DRIVE_PATH = re.compile(r"^\s*(?P<keep>[A-Za-z]:[\\/])[\\/]*\S")
_HOME_PATH = re.compile(r"^\s*(?P<keep>/(?:home|Users)/)[^/\s]+/\S")


def _ignore_pattern(value: str) -> str:
    """A `.gitignore`-style name: `bin/` (a folder) or `*.user` (any file or folder)."""
    if not value.strip() or value != value.strip():
        raise ValueError("must not be blank or have surrounding spaces")
    if "\\" in value or value.startswith(("/", "!")):
        raise ValueError("must be a name such as bin/ or *.user, without \\, a leading / or !")
    if "/" in value.rstrip("/") or value.endswith("//"):
        raise ValueError("may contain / only at the end (a folder)")
    return value


def _suffix(value: str) -> str:
    if not re.fullmatch(r"\.[A-Za-z0-9]+", value):
        raise ValueError("must be a file suffix such as .cs")
    return value


IgnorePattern = Annotated[str, AfterValidator(_ignore_pattern)]
Suffix = Annotated[str, AfterValidator(_suffix)]


class GitignoreCoversParams(BaseModel):
    """`patterns`: what `.gitignore` must leave out; `how_to_add`: an optional hint shown
    after "Add a `.gitignore`", in parentheses."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: tuple[IgnorePattern, ...] = Field(min_length=1)
    how_to_add: str = ""


class NotCommittedParams(BaseModel):
    """`patterns`: build or IDE files that must not be tracked by git."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    patterns: tuple[IgnorePattern, ...] = Field(min_length=1)


class NoAbsolutePathsParams(BaseModel):
    """`suffixes`: the source files to read, such as `.cs` and `.js`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    suffixes: tuple[Suffix, ...] = Field(min_length=1)


def matched_unit(pattern: str, path: str) -> str | None:
    """What of the tracked `path` the `.gitignore`-style `pattern` names, ignoring case.

    A folder pattern (`bin/`) names the first folder on the path with that name, as
    `App/bin/`; a pattern without a slash names a folder or the file itself. None when the
    pattern does not match the path.
    """
    segments = path.split("/")
    name = pattern.rstrip("/").casefold()
    candidates = segments[:-1] if pattern.endswith("/") else segments
    for index, segment in enumerate(candidates):
        if fnmatchcase(segment.casefold(), name):
            unit = "/".join(segments[: index + 1])
            return unit if index == len(segments) - 1 else f"{unit}/"
    return None


def checked_files(check_input: CheckInput) -> tuple[str, ...]:
    """The files a check looks at (`repo_tree`, without bin/, obj/ and .git/): a given file
    list as it is; otherwise the working copy's files that git tracks, or every file when
    git's state was not read."""
    files = repo_tree(check_input).files
    if check_input.files is not None or check_input.tracked is None:
        return files
    tracked = set(check_input.tracked.paths)
    return tuple(path for path in files if path in tracked)


def read_source(repo_dir: Path, path: str) -> str | None:
    """The text of the file `path`; None when it is too large or cannot be read."""
    file = repo_dir / path
    try:
        if file.stat().st_size > MAX_SOURCE_BYTES:
            return None
        return file.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None


def joined_examples(items: Iterable[Localized]) -> Localized:
    """Join localized examples with "; "."""
    examples = list(items)
    return Localized(he="; ".join(e.he for e in examples), en="; ".join(e.en for e in examples))


def code(text: str) -> str:
    """`text` in backticks, for a message."""
    return f"`{text}`"


@check(
    "gitignore_covers",
    GitignoreCoversParams,
    "A .gitignore exists (at the root or by a solution or project), and every tracked "
    "file matching patterns is left out by a .gitignore (git decides).",
)
def gitignore_covers(
    check_input: CheckInput, params: GitignoreCoversParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    """One finding when there is no `.gitignore`; otherwise one listing what is tracked,
    matches a pattern, and no `.gitignore` leaves out. A pattern nothing in the repo
    matches passes."""
    tracked = check_input.tracked
    if tracked is None:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    if not _has_gitignore(tracked):
        hint = f" ({params.how_to_add})" if params.how_to_add else ""
        message = GITIGNORE_MISSING.render(how_to_add=hint)
        return (deterministic_finding(check_input, message, ("no .gitignore",)),)
    ignored = set(tracked.ignored)
    uncovered: dict[str, str] = {}
    for path in tracked.paths:
        if path in ignored:
            continue
        for pattern in params.patterns:
            unit = matched_unit(pattern, path)
            if unit is not None:
                uncovered.setdefault(unit, pattern)
                break
    if not uncovered:
        return ()
    patterns = list(dict.fromkeys(uncovered[unit] for unit in sorted(uncovered)))
    message = GITIGNORE_NOT_COVERING.render(
        examples=", ".join(code(unit) for unit in sorted(uncovered)[:MAX_EXAMPLES]),
        count=len(uncovered),
        patterns=", ".join(code(pattern) for pattern in patterns),
    )
    return (deterministic_finding(check_input, message, sorted(uncovered)),)


@check(
    "not_committed",
    NotCommittedParams,
    "No tracked file matches patterns (even one .gitignore lists now: it was committed).",
)
def not_committed(
    check_input: CheckInput, params: NotCommittedParams
) -> tuple[Finding, ...] | NotEvaluatedReason:
    """One finding listing the tracked folders and files that match a pattern."""
    tracked = check_input.tracked
    if tracked is None:
        return NotEvaluatedReason.ENVIRONMENT_ISSUE
    units = sorted(
        {
            unit
            for path in tracked.paths
            for pattern in params.patterns
            if (unit := matched_unit(pattern, path)) is not None
        }
    )
    if not units:
        return ()
    message = NOT_COMMITTED.render(
        examples=", ".join(code(unit) for unit in units[:MAX_EXAMPLES]),
        count=len(units),
        first=units[0],
    )
    return (deterministic_finding(check_input, message, units),)


def _has_gitignore(tracked: TrackedFiles) -> bool:
    """A tracked `.gitignore` at the root, or in a folder holding (or above) a solution
    or project file."""
    folders = {_folder(path) for path in tracked.paths if PurePosixPath(path).name == GITIGNORE}
    if "" in folders:
        return True
    projects = {_folder(path) for path in tracked.paths if path.lower().endswith(PROJECT_SUFFIXES)}
    return any(
        project == folder or project.startswith(f"{folder}/")
        for folder in folders
        for project in projects
    )


def _folder(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return "" if parent == "." else parent


# ---------------------------------------------------------------- full disk paths


def masked_path(text: str) -> str | None:
    """`C:\\…` or `/home/…` when the literal `text` is a full disk path; None otherwise.

    One separator is kept, so a C# `"C:\\\\x"` (escaped backslash) shows as `C:\\…`."""
    for pattern in (_DRIVE_PATH, _HOME_PATH):
        match = pattern.match(text)
        if match is not None:
            return f"{match['keep']}{MASK}"
    return None


@check(
    "no_absolute_paths",
    NoAbsolutePathsParams,
    "No string literal in a source file with one of suffixes is a full disk path "
    "(C:\\..., D:/..., /home/..., /Users/...).",
)
def no_absolute_paths(
    check_input: CheckInput, params: NoAbsolutePathsParams
) -> tuple[Finding, ...]:
    """One finding listing the literals that start with a full disk path, masked after the
    drive or first folder. Comments are skipped; a path only inside a longer text (such as
    a connection string) is not reported."""
    suffixes = tuple(suffix.lower() for suffix in params.suffixes)
    found: list[tuple[str, int, str, SourceScan, Literal]] = []
    for path in checked_files(check_input):
        if not path.lower().endswith(suffixes):
            continue
        text = read_source(check_input.repo_dir, path)
        if text is None:
            continue
        scan = scan_source(text)
        for literal in scan.literals:
            first = literal.parts[0]
            masked = masked_path(first) if first is not None else None
            if masked is not None:
                found.append((path, scan.line_of(literal.start), masked, scan, literal))
    if not found:
        return ()
    examples = [
        LOCATED_EXAMPLE.render(item=code(masked), file=path, line=line)
        for path, line, masked, _, _ in found[:MAX_EXAMPLES]
    ]
    message = _with_examples(ABSOLUTE_PATHS, examples, len(found))
    path, line, masked, scan, literal = found[0]
    facts = [f"{item_path}:{item_line}" for item_path, item_line, *_ in found]
    evidence = _masked_evidence(path, line, masked, scan, literal)
    return (deterministic_finding(check_input, message, facts, evidence),)


def _with_examples(template: MessageTemplate, examples: list[Localized], count: int) -> Localized:
    return template.render(examples=joined_examples(examples), count=count)


def _masked_evidence(
    path: str, line: int, masked: str, scan: SourceScan, literal: Literal
) -> Evidence | None:
    """The literal's line, with the literal's text replaced by the masked path."""
    text = scan.text
    line_start = text.rfind("\n", 0, literal.start) + 1
    line_end = text.find("\n", literal.start)
    line_end = len(text) if line_end < 0 else line_end
    content_start = text.find(literal.text, literal.start)
    content_end = content_start + len(literal.text)
    quoted = text[line_start:content_start] + masked + text[min(content_end, line_end) : line_end]
    quote = quoted.strip()
    if not quote:
        return None
    try:
        return Evidence(file_path=path, line_start=line, line_end=line, quote=quote)
    except PydanticValidationError:
        return None
