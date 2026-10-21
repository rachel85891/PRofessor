"""Structure checks: read the working copy only, never execute anything from it."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from hw_checker.checks.deterministic.findings import deterministic_finding
from hw_checker.checks.deterministic.messages import FILE_MISSING, FILE_TOO_FEW, FOLDER_MISSING
from hw_checker.checks.registry import check
from hw_checker.domain import CheckInput, Finding

IGNORED_DIRS = frozenset({"bin", "obj", ".git"})
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_GLOB_CHARACTERS = frozenset("*?[")


def _relative_inside_repo(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    if "\\" in value:
        raise ValueError("must use forward slashes")
    if value.startswith("/") or _DRIVE_LETTER.match(value):
        raise ValueError("must be relative to the repository root")
    if ".." in value.split("/"):
        raise ValueError("must not contain '..'")
    return value


def _plain_path(value: str) -> str:
    if _GLOB_CHARACTERS & set(value):
        raise ValueError("must be a folder path, without wildcards")
    return value.strip("/")


RepoGlob = Annotated[str, AfterValidator(_relative_inside_repo)]
RepoFolder = Annotated[str, AfterValidator(_relative_inside_repo), AfterValidator(_plain_path)]


class FileExistsParams(BaseModel):
    """`glob`: a pattern relative to the repo root (`*`, `**`, `?`); `min_count`: at least."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    glob: RepoGlob
    min_count: int = Field(default=1, ge=1)


class FolderExistsParams(BaseModel):
    """`path`: a folder path such as `Controllers` or `src/Models`, found at any depth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: RepoFolder


@dataclass(frozen=True)
class RepoTree:
    """Repo-relative POSIX paths of the files and folders that checks may look at."""

    files: tuple[str, ...]
    folders: tuple[str, ...]


def scan_repo(root: Path) -> RepoTree:
    """List the working copy without bin/, obj/ and .git/, never leaving `root`.

    Symlinks and junctions are skipped, and so is anything that resolves outside `root`.
    """
    resolved_root = root.resolve()
    files: list[str] = []
    folders: list[str] = []
    for directory, subdirectories, names in os.walk(resolved_root, followlinks=False):
        current = Path(directory)
        kept = []
        for name in sorted(subdirectories):
            path = current / name
            if name.lower() in IGNORED_DIRS or not _stays_inside(path, resolved_root):
                continue
            kept.append(name)
            folders.append(path.relative_to(resolved_root).as_posix())
        subdirectories[:] = kept
        files.extend(
            (current / name).relative_to(resolved_root).as_posix()
            for name in sorted(names)
            if _stays_inside(current / name, resolved_root)
        )
    return RepoTree(tuple(files), tuple(folders))


def _stays_inside(path: Path, root: Path) -> bool:
    if path.is_symlink() or path.is_junction():
        return False
    return path.resolve().is_relative_to(root)


def glob_pattern(glob: str) -> re.Pattern[str]:
    """Compile a repo glob, matched case-insensitively.

    `**/` matches any number of folders (including none), `**` anything, `*` anything within
    one path segment, and `?` one character within a segment.
    """
    parts: list[str] = []
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            parts.append(".*")
            index += 2
        elif glob[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(glob[index]))
            index += 1
    return re.compile("".join(parts), re.IGNORECASE)


@check(
    "file_exists",
    FileExistsParams,
    "At least min_count files match glob (case-insensitive; bin/, obj/ and .git/ are ignored).",
)
def file_exists(check_input: CheckInput, params: FileExistsParams) -> tuple[Finding, ...]:
    """Pass when at least `min_count` files in the working copy match `glob`."""
    pattern = glob_pattern(params.glob)
    found = sum(1 for path in scan_repo(check_input.repo_dir).files if pattern.fullmatch(path))
    if found >= params.min_count:
        return ()
    message = (
        FILE_MISSING.render(glob=params.glob)
        if found == 0
        else FILE_TOO_FEW.render(glob=params.glob, min_count=params.min_count, found=found)
    )
    facts = (f"glob={params.glob}", f"min_count={params.min_count}", f"found={found}")
    return (deterministic_finding(check_input, message, facts),)


@check(
    "folder_exists",
    FolderExistsParams,
    "A folder with this path exists at any depth (case-insensitive; bin/, obj/, .git/ ignored).",
)
def folder_exists(check_input: CheckInput, params: FolderExistsParams) -> tuple[Finding, ...]:
    """Pass when a folder whose path ends with `path` exists; projects may sit in a subfolder."""
    target = params.path.lower()
    folders = scan_repo(check_input.repo_dir).folders
    if any(folder.lower() == target or folder.lower().endswith(f"/{target}") for folder in folders):
        return ()
    message = FOLDER_MISSING.render(path=params.path)
    return (deterministic_finding(check_input, message, (f"path={params.path}",)),)
