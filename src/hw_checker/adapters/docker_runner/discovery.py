"""Finds the ASP.NET Core Web project in an untrusted student working copy.

Only reads `.csproj` files as XML text; nothing from the repo is executed or evaluated.
With exactly one `Microsoft.NET.Sdk.Web` project, that is the one; with several, the failure
lists them as candidates, and `web_choice` picks one by its rule.
"""

import os
import posixpath
import re
import xml.etree.ElementTree as ElementTree
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

WEB_SDK = "microsoft.net.sdk.web"
EXCLUDED_DIRS = frozenset({"bin", "obj", ".git"})
MAX_PROJECT_FILE_BYTES = 1_000_000
_FRAMEWORK_VERSION = re.compile(r"^net(?P<major>\d+)\.(?P<minor>\d+)$")
_SAFE_ASSEMBLY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ProjectFolder:
    """A project of the solution: its folder relative to the working copy ("" at the root),
    and whether `<Nullable>` is `enable` in it."""

    folder: str
    nullable: bool


@dataclass(frozen=True)
class WebProject:
    """The Web project to build: its path relative to the working copy, and its assembly.

    `target_framework` is the literal `<TargetFramework>` (or `<TargetFrameworks>`) text,
    None when the project file has neither. `nullable` is True when `<Nullable>` is `enable`,
    which makes `[ApiController]` treat non-nullable reference properties as required.
    """

    path: str
    assembly_name: str
    unreadable: tuple[str, ...] = ()
    target_framework: str | None = None
    nullable: bool = False
    other_projects: tuple[ProjectFolder, ...] = ()
    """The solution's other projects (class libraries such as entities), where request
    body models may live."""
    references: tuple[str, ...] = ()
    """The repo's projects (paths relative to the working copy) this one references with
    `<ProjectReference>`."""

    @property
    def folder(self) -> str:
        """The folder holding the project file, relative to the working copy ("" at the root)."""
        return self.path.rpartition("/")[0]

    @property
    def framework(self) -> str | None:
        """The target framework in lower case, without surrounding spaces."""
        return None if self.target_framework is None else self.target_framework.strip().lower()

    def framework_problem(self, supported: Collection[str]) -> str | None:
        """Why no pinned images can build this project, or None when some can."""
        names = ", ".join(supported)
        written = self.target_framework
        if written is None:
            return f"no <TargetFramework> found in {self.path}; supported: {names}"
        if written.strip().lower() not in supported:
            return f"{self.path} targets {written.strip()}; supported: {names}"
        return None


def framework_version(framework: str) -> tuple[int, int] | None:
    """`net8.0` gives (8, 0); anything else (such as `net10.0-windows`) gives None."""
    match = _FRAMEWORK_VERSION.match(framework)
    return None if match is None else (int(match["major"]), int(match["minor"]))


def is_older_framework(framework: str | None, course_framework: str) -> bool:
    """True when `framework` is a plain .NET version older than the course's."""
    version = None if framework is None else framework_version(framework)
    course = framework_version(course_framework)
    return version is not None and course is not None and version < course


@dataclass(frozen=True)
class DiscoveryFailure:
    """No single Web project was found; `message` names everything that was found."""

    message: str
    web_projects: tuple[str, ...] = ()
    other_projects: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    candidates: tuple[WebProject, ...] = ()
    """With several Web projects: each of them, for `web_choice` to pick from."""


@dataclass(frozen=True)
class _ParsedProject:
    path: str
    is_web: bool
    assembly_name: str
    target_framework: str | None
    nullable: bool
    references: tuple[str, ...] = ()
    """The `Include` of each `<ProjectReference>`, as written."""


def discover_web_project(working_copy: Path) -> WebProject | DiscoveryFailure:
    """Return the single `Microsoft.NET.Sdk.Web` project, or why there isn't exactly one.

    `bin/`, `obj/` and `.git/` folders are ignored, and symlinked folders are not followed.
    """
    parsed: list[_ParsedProject] = []
    unreadable: list[str] = []
    for project_file in _project_files(working_copy):
        relative = project_file.relative_to(working_copy).as_posix()
        project = _parse_project(project_file, relative)
        if project is None:
            unreadable.append(relative)
        else:
            parsed.append(project)
    web = [project for project in parsed if project.is_web]
    others = tuple(project.path for project in parsed if not project.is_web)
    candidates = tuple(_web_project(project, parsed, unreadable) for project in web)
    if len(candidates) == 1:
        return candidates[0]
    return DiscoveryFailure(
        message=_failure_message(web, others, unreadable),
        web_projects=tuple(project.path for project in web),
        other_projects=others,
        unreadable=tuple(unreadable),
        candidates=candidates if len(candidates) > 1 else (),
    )


def _web_project(
    project: _ParsedProject, parsed: list[_ParsedProject], unreadable: list[str]
) -> WebProject:
    """`project` as the Web project to build, with every other project of the repo."""
    return WebProject(
        project.path,
        project.assembly_name,
        tuple(unreadable),
        project.target_framework,
        project.nullable,
        tuple(
            ProjectFolder(other.path.rpartition("/")[0], other.nullable)
            for other in parsed
            if other is not project
        ),
        _resolved_references(project, [other.path for other in parsed]),
    )


def _resolved_references(project: _ParsedProject, paths: list[str]) -> tuple[str, ...]:
    """The repo's projects that `project`'s `<ProjectReference>` items name, compared
    ignoring case and with `\\` read as a separator, as on Windows."""
    folder = project.path.rpartition("/")[0]
    by_lower = {path.lower(): path for path in paths}
    found: dict[str, None] = {}
    for include in project.references:
        joined = posixpath.normpath(posixpath.join(folder or ".", include.replace("\\", "/")))
        match = by_lower.get(joined.lower())
        if match is not None and match != project.path:
            found.setdefault(match)
    return tuple(found)


def _project_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, files in os.walk(root, followlinks=False):
        subdirectories[:] = sorted(
            name
            for name in subdirectories
            if name.lower() not in EXCLUDED_DIRS and not Path(directory, name).is_symlink()
        )
        found.extend(
            Path(directory, name)
            for name in sorted(files)
            if name.lower().endswith(".csproj") and not Path(directory, name).is_symlink()
        )
    return found


def _parse_project(path: Path, relative: str) -> _ParsedProject | None:
    try:
        if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
            return None
        root = ElementTree.fromstring(path.read_bytes())
    except (OSError, ElementTree.ParseError):
        return None
    return _ParsedProject(
        relative,
        _is_web_project(root),
        _assembly_name(root, path),
        _target_framework(root),
        _nullable_enabled(root),
        _project_references(root),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _is_web_project(root: ElementTree.Element) -> bool:
    sdks = [root.get("Sdk", "")]
    sdks += [
        element.get("Name", "") for element in root.iter() if _local_name(element.tag) == "Sdk"
    ]
    return any(
        sdk.split("/", 1)[0].strip().lower() == WEB_SDK
        for value in sdks
        for sdk in value.split(";")
    )


def _assembly_name(root: ElementTree.Element, path: Path) -> str:
    for element in root.iter():
        if _local_name(element.tag) == "AssemblyName":
            name = (element.text or "").strip()
            # A literal name is used as is; an MSBuild expression is not evaluated.
            if _SAFE_ASSEMBLY_NAME.match(name):
                return name
    return path.stem


def _target_framework(root: ElementTree.Element) -> str | None:
    for name in ("TargetFramework", "TargetFrameworks"):
        for element in root.iter():
            if _local_name(element.tag) == name and (element.text or "").strip():
                return (element.text or "").strip()
    return None


def _project_references(root: ElementTree.Element) -> tuple[str, ...]:
    return tuple(
        include
        for element in root.iter()
        if _local_name(element.tag) == "ProjectReference"
        and (include := (element.get("Include") or "").strip())
    )


def _nullable_enabled(root: ElementTree.Element) -> bool:
    return any(
        _local_name(element.tag) == "Nullable" and (element.text or "").strip().lower() == "enable"
        for element in root.iter()
    )


def _failure_message(
    web: list[_ParsedProject], others: tuple[str, ...], unreadable: list[str]
) -> str:
    if len(web) > 1:
        message = f"several Web projects found: {', '.join(project.path for project in web)}"
    elif others:
        message = f'no Web project (Sdk="Microsoft.NET.Sdk.Web") among: {", ".join(others)}'
    elif unreadable:
        message = "no readable .csproj file found"
    else:
        message = "no .csproj file found (bin/, obj/ and .git/ are ignored)"
    if unreadable:
        message += f"; unreadable project files: {', '.join(unreadable)}"
    return message
