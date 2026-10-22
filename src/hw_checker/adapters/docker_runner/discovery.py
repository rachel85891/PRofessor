"""Finds the single ASP.NET Core Web project in an untrusted student working copy.

Only reads `.csproj` files as XML text; nothing from the repo is executed or evaluated.
"""

import os
import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

WEB_SDK = "microsoft.net.sdk.web"
EXCLUDED_DIRS = frozenset({"bin", "obj", ".git"})
MAX_PROJECT_FILE_BYTES = 1_000_000
SUPPORTED_FRAMEWORK = "net10.0"
"""The only target framework the pinned images build and run (.NET 10 only)."""
_SAFE_ASSEMBLY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class WebProject:
    """The Web project to build: its path relative to the working copy, and its assembly.

    `target_framework` is the literal `<TargetFramework>` (or `<TargetFrameworks>`) text,
    None when the project file has neither.
    """

    path: str
    assembly_name: str
    unreadable: tuple[str, ...] = ()
    target_framework: str | None = None

    @property
    def folder(self) -> str:
        """The folder holding the project file, relative to the working copy ("" at the root)."""
        return self.path.rpartition("/")[0]

    def framework_problem(self) -> str | None:
        """Why the pinned images cannot build this project, or None when they can."""
        if self.target_framework is None:
            return (
                f"no <TargetFramework> found in {self.path}; "
                f"only {SUPPORTED_FRAMEWORK} is supported"
            )
        if self.target_framework.strip().lower() != SUPPORTED_FRAMEWORK:
            return (
                f"{self.path} targets {self.target_framework.strip()}; "
                f"only {SUPPORTED_FRAMEWORK} is supported"
            )
        return None


@dataclass(frozen=True)
class DiscoveryFailure:
    """No single Web project was found; `message` names everything that was found."""

    message: str
    web_projects: tuple[str, ...] = ()
    other_projects: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ParsedProject:
    path: str
    is_web: bool
    assembly_name: str
    target_framework: str | None


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
    if len(web) == 1:
        return WebProject(
            web[0].path, web[0].assembly_name, tuple(unreadable), web[0].target_framework
        )
    return DiscoveryFailure(
        message=_failure_message(web, others, unreadable),
        web_projects=tuple(project.path for project in web),
        other_projects=others,
        unreadable=tuple(unreadable),
    )


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
        relative, _is_web_project(root), _assembly_name(root, path), _target_framework(root)
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
