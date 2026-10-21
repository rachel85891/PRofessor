"""Roster models: students, pairs, repos to check, raw roster rows and warnings."""

import re
from enum import StrEnum
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr

MAX_PAIR_MEMBERS = 2

GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
REPO_ID_SEPARATOR = "__"
MAX_OWNER_LENGTH = 39
MAX_REPO_NAME_LENGTH = 100
_OWNER_AND_NAME = 2

# GitHub owners: letters, digits and inner hyphens. Repo names: letters, digits, '.', '_', '-'.
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_REPO_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SCHEMELESS_PATTERN = re.compile(r"^(?:www\.)?github\.com/", re.IGNORECASE)
_SSH_PATTERN = re.compile(r"^git@github\.com:(?P<path>[^:]+)$", re.IGNORECASE)


class Student(DomainModel):
    """A student, identified by their GitHub repo URL (normalized in roster ingestion)."""

    name: NonBlankStr
    github_url: NonBlankStr


class Pair(DomainModel):
    """One or two students who work together. A student working alone is a pair of one."""

    pair_id: Identifier
    members: tuple[Student, ...] = Field(min_length=1, max_length=MAX_PAIR_MEMBERS)

    @model_validator(mode="after")
    def _members_are_distinct(self) -> Self:
        urls = [member.github_url for member in self.members]
        if len(set(urls)) != len(urls):
            raise ValueError("pair members must have distinct github_url values")
        return self


class RepoTarget(DomainModel):
    """A single repo to check: the unit of work."""

    repo_id: Identifier
    owner: NonBlankStr
    name: NonBlankStr
    url: NonBlankStr
    student: Student
    pair_id: Identifier


class RosterRow(DomainModel):
    """One raw roster row as read from the source, before pair merging."""

    name: NonBlankStr
    partner_name: NonBlankStr | None = None
    github_url: str
    row_number: int = Field(ge=1)


class RosterWarningKind(StrEnum):
    """Problems found while turning roster rows into pairs."""

    MISSING_PARTNER = "missing_partner"
    INVALID_URL = "invalid_url"
    ASYMMETRIC_DUPLICATE = "asymmetric_duplicate"
    DUPLICATE_REPO = "duplicate_repo"
    AMBIGUOUS_PARTNER = "ambiguous_partner"


class RosterWarning(DomainModel):
    """A roster problem shown to the reviewer; ingestion continues past it."""

    kind: RosterWarningKind
    message: NonBlankStr
    row: int | None = Field(default=None, ge=1)


class RosterResult(DomainModel):
    """The outcome of loading a roster: repos to check, their pairs, and every warning."""

    repos: tuple[RepoTarget, ...]
    pairs: tuple[Pair, ...]
    warnings: tuple[RosterWarning, ...]


class GitHubRepo(DomainModel):
    """A parsed GitHub repo reference in canonical, lowercase form."""

    owner: NonBlankStr
    name: NonBlankStr
    url: NonBlankStr
    repo_id: Identifier


def parse_github_url(text: str) -> GitHubRepo | None:
    """Parse a GitHub repo URL into its canonical form, or return None if it is not one.

    Accepts https/http URLs on github.com or www.github.com (any host case), the same
    without a scheme (treated as https), a trailing slash or `.git`, extra path segments
    (only the first two are used), query strings, fragments, SSH URLs
    (`git@github.com:owner/name.git`) and surrounding whitespace.
    The canonical URL is `https://github.com/<owner>/<name>` and the repo id is
    `<owner>__<name>`, both lowercase. Owners cannot contain `_`, so the id is unambiguous.
    """
    segments = _repo_path_segments(text.strip())
    if segments is None or len(segments) < _OWNER_AND_NAME:
        return None
    owner, name = segments[0], _strip_git_suffix(segments[1])
    if not _is_valid_owner(owner) or not _is_valid_repo_name(name):
        return None
    owner, name = owner.lower(), name.lower()
    return GitHubRepo(
        owner=owner,
        name=name,
        url=f"https://github.com/{owner}/{name}",
        repo_id=f"{owner}{REPO_ID_SEPARATOR}{name}",
    )


def _repo_path_segments(text: str) -> list[str] | None:
    ssh = _SSH_PATTERN.match(text)
    if ssh:
        segments = ssh.group("path").strip("/").split("/")
        return segments if len(segments) == _OWNER_AND_NAME else None
    if _SCHEMELESS_PATTERN.match(text):
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if parts.username or parts.password or parts.port is not None:
        return None
    if (parts.hostname or "") not in GITHUB_HOSTS:
        return None
    segments = parts.path.strip("/").split("/")
    return None if "" in segments[:2] else segments


def _strip_git_suffix(name: str) -> str:
    return name[: -len(".git")] if name.lower().endswith(".git") else name


def _is_valid_owner(owner: str) -> bool:
    return len(owner) <= MAX_OWNER_LENGTH and bool(_OWNER_PATTERN.match(owner))


def _is_valid_repo_name(name: str) -> bool:
    return (
        len(name) <= MAX_REPO_NAME_LENGTH
        and name not in {".", ".."}
        and bool(_REPO_NAME_PATTERN.match(name))
    )
