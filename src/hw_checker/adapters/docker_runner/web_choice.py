"""Chooses the Web project to run when a repo has several (stage 22).

A student who moves to layers often keeps the old single-project API next to the new
layered one, so the repo has two `Microsoft.NET.Sdk.Web` projects. The rule, a mechanism
of the runner and not assignment data, narrows the candidates step by step:

1. keep those with an entry point: a `Program.cs`, or C# that builds the host
   (`WebApplication.CreateBuilder`, `Host.CreateDefaultBuilder`, `WebHost.CreateDefaultBuilder`)
   or declares `Main`;
2. then prefer the one that references other projects of the repo;
3. then the one whose files the latest commit that changed any of them changed.

When one step leaves a single project, it is chosen, and the step is the reason. A step
that leaves none keeps the candidates it started with. When the last step still leaves
several, nothing is chosen, and the failure names the candidates. The source is read as
text; nothing is compiled or run.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from hw_checker.adapters.docker_runner.csharp_source import (
    mask_source,
    source_files,
)
from hw_checker.adapters.docker_runner.discovery import WebProject
from hw_checker.domain import WebProjectChoiceReason

PROGRAM_FILE = "program.cs"
_ENTRY_POINT = re.compile(
    r"\b(?:WebApplication\s*\.\s*CreateBuilder|(?:Web)?Host\s*\.\s*CreateDefaultBuilder)\s*\("
    r"|\bstatic\s+(?:async\s+)?[\w<>]+\s+Main\s*\("
)


@dataclass(frozen=True)
class WebChoice:
    """The chosen project and why."""

    project: WebProject
    reason: WebProjectChoiceReason


def choose_web_project(
    working_copy: Path,
    candidates: Sequence[WebProject],
    recent_changes: Sequence[Sequence[str]],
) -> WebChoice | str:
    """The Web project to run among `candidates`, or why none could be chosen.

    `recent_changes` lists the paths each of the latest commits changed, newest first.
    """
    remaining = list(candidates)
    folders = [candidate.folder for candidate in candidates]

    def has_entry_point(candidate: WebProject) -> bool:
        return _has_entry_point(working_copy, candidate, folders)

    def references_projects(candidate: WebProject) -> bool:
        return bool(candidate.references)

    steps: tuple[tuple[WebProjectChoiceReason, Callable[[WebProject], bool]], ...] = (
        (WebProjectChoiceReason.ENTRY_POINT, has_entry_point),
        (WebProjectChoiceReason.REFERENCES_PROJECTS, references_projects),
    )
    for reason, keeps in steps:
        kept = [candidate for candidate in remaining if keeps(candidate)]
        if len(kept) == 1:
            return WebChoice(kept[0], reason)
        remaining = kept or remaining
    latest = _latest_changed(remaining, list(candidates), recent_changes)
    if latest is not None:
        return WebChoice(latest, WebProjectChoiceReason.RECENTLY_CHANGED)
    names = ", ".join(candidate.path for candidate in remaining)
    return (
        f"several Web projects found: {', '.join(c.path for c in candidates)}; none could be "
        f"chosen: {names} all have an entry point and the same project references, and no "
        "recent commit changed only one of them"
    )


def _has_entry_point(working_copy: Path, project: WebProject, folders: list[str]) -> bool:
    for path in _own_sources(working_copy, project, folders):
        if path.name.lower() == PROGRAM_FILE:
            return True
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _ENTRY_POINT.search(mask_source(text).text):
            return True
    return False


def _own_sources(working_copy: Path, project: WebProject, folders: list[str]) -> list[Path]:
    """The project's `.cs` files, without those of another candidate nested inside it."""
    root = working_copy / project.folder
    nested = [
        folder for folder in folders if folder != project.folder and _inside(folder, project.folder)
    ]
    return [
        path
        for path in source_files(root)
        if not any(_inside(path.relative_to(working_copy).as_posix(), folder) for folder in nested)
    ]


def _latest_changed(
    remaining: list[WebProject],
    candidates: list[WebProject],
    recent_changes: Sequence[Sequence[str]],
) -> WebProject | None:
    """The only one of `remaining` that the newest commit touching any of them changed;
    None when that commit changed several, or no commit changed any. A file belongs to the
    candidate (of all `candidates`) whose folder holds it most deeply."""
    names = {candidate.path for candidate in remaining}
    for changed in recent_changes:
        owners = {
            owner.path
            for path in changed
            if (owner := _owner(path, candidates)) is not None and owner.path in names
        }
        if len(owners) > 1:
            return None
        if owners:
            (only,) = owners
            return next(candidate for candidate in remaining if candidate.path == only)
    return None


def _owner(path: str, candidates: list[WebProject]) -> WebProject | None:
    """The candidate whose folder holds `path` most deeply (ignoring case), if any."""
    holding = [candidate for candidate in candidates if _inside(path, candidate.folder)]
    return max(holding, key=lambda candidate: len(candidate.folder), default=None)


def _inside(path: str, folder: str) -> bool:
    return folder == "" or path.lower().startswith(folder.lower() + "/")
