"""Roster models: students, pairs, repos to check, raw roster rows and warnings."""

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr

MAX_PAIR_MEMBERS = 2


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


class RosterWarning(DomainModel):
    """A roster problem shown to the reviewer; ingestion continues past it."""

    kind: RosterWarningKind
    message: NonBlankStr
    row: int | None = Field(default=None, ge=1)
