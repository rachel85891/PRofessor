"""A project's layer role, read from the assignment's data (ADR 0023: roles are params).

`roles` maps a role name (`api`, `services`) to how its project is recognized: an SDK the
project file names, or a pattern on the project file's name. A project matching no role,
or more than one, is unassigned and listed; it is never guessed. A file belongs to the
project whose folder is its nearest parent.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from hw_checker.domain import ArchitectureEvidence


def _regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as error:
        raise ValueError(f"not a valid regular expression ({error})") from error
    return value


Regex = Annotated[str, AfterValidator(_regex)]
"""A regular expression, checked when the params are read."""


class RolePattern(BaseModel):
    """How a role's project is recognized: `sdk`, an SDK the project file names (it wins
    over the name), and `name`, a pattern searched in the project file's name without
    `.csproj`, ignoring case."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sdk: str = ""
    name: Regex = ""

    @model_validator(mode="after")
    def _says_something(self) -> Self:
        if not self.sdk and not self.name:
            raise ValueError("give sdk, name or both")
        return self


Roles = Annotated[dict[str, RolePattern], Field(min_length=1)]
"""The layer roles, by name."""


@dataclass(frozen=True)
class Layers:
    """The repo's projects by role. `unassigned` are the project files that match no role
    or more than one."""

    by_role: Mapping[str, tuple[str, ...]]
    unassigned: tuple[str, ...]
    role_of_project: Mapping[str, str]

    def projects(self, role: str) -> tuple[str, ...]:
        """The project files of `role`; empty when it has none (or is not a role)."""
        return self.by_role.get(role, ())

    def missing(self, roles: tuple[str, ...]) -> tuple[str, ...]:
        """The `roles` with no project, in their order."""
        return tuple(role for role in roles if not self.projects(role))

    def project_of(self, path: str) -> str | None:
        """The project file whose folder is the nearest parent of `path`; None when no
        project folder holds it, or two projects share that folder."""
        folders = [
            (_folder(project), project)
            for project in (*self.role_of_project, *self.unassigned)
            if _holds(_folder(project), path)
        ]
        if not folders:
            return None
        deepest = max(len(folder) for folder, _ in folders)
        nearest = [project for folder, project in folders if len(folder) == deepest]
        return nearest[0] if len(nearest) == 1 else None

    def role_of(self, path: str) -> str | None:
        """The role of the project holding `path`; None when it is unassigned or in none."""
        project = self.project_of(path)
        return None if project is None else self.role_of_project.get(project)


def layers(evidence: ArchitectureEvidence, roles: Mapping[str, RolePattern]) -> Layers:
    """Assign each project file of `evidence` its role by `roles`."""
    sdks = {project.file_path: project.sdk_references for project in evidence.projects}
    by_role: dict[str, list[str]] = {role: [] for role in roles}
    role_of_project: dict[str, str] = {}
    unassigned: list[str] = []
    for project in evidence.project_files:
        role = _role(project, sdks.get(project, ()), roles)
        if role is None:
            unassigned.append(project)
        else:
            by_role[role].append(project)
            role_of_project[project] = role
    return Layers(
        by_role={role: tuple(projects) for role, projects in by_role.items()},
        unassigned=tuple(unassigned),
        role_of_project=role_of_project,
    )


def project_name(project: str) -> str:
    """The project file's name without `.csproj` (`Services`)."""
    return PurePosixPath(project).stem


def _role(project: str, sdks: tuple[str, ...], roles: Mapping[str, RolePattern]) -> str | None:
    by_sdk = [role for role, pattern in roles.items() if pattern.sdk and pattern.sdk in sdks]
    if by_sdk:
        return by_sdk[0] if len(by_sdk) == 1 else None
    stem = project_name(project)
    by_name = [
        role
        for role, pattern in roles.items()
        if pattern.name and re.search(pattern.name, stem, re.IGNORECASE)
    ]
    return by_name[0] if len(by_name) == 1 else None


def _folder(project: str) -> str:
    parent = PurePosixPath(project).parent.as_posix()
    return "" if parent == "." else parent


def _holds(folder: str, path: str) -> bool:
    return not folder or path.startswith(f"{folder}/")
