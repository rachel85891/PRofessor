"""Version-control models used by the Git and code-host ports."""

from pathlib import Path

from pydantic import AwareDatetime, Field

from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr


class CommitInfo(DomainModel):
    """One commit from a repo's history (shown raw in v1; AI-use signals come later)."""

    sha: Identifier
    author_name: str
    authored_at: AwareDatetime
    message: str


class PullRequestRef(DomainModel):
    """A reference to a pull request opened or updated on the code host."""

    number: int = Field(ge=1)
    url: NonBlankStr
    created: bool
    """True when the pull request was opened now; False when an open one was updated."""


class BranchHead(DomainModel):
    """A repo's default branch and the commit it points to."""

    name: NonBlankStr
    sha: Identifier


class ForkRef(DomainModel):
    """The publishing account's fork of a student repo, which review branches are pushed to."""

    owner: NonBlankStr
    name: NonBlankStr
    push_url: NonBlankStr

    @property
    def full_name(self) -> str:
        """`owner/name`."""
        return f"{self.owner}/{self.name}"


class FetchedRepo(DomainModel):
    """A student repo's local working copy, at the commit that is checked."""

    working_copy: Path
    commit_sha: Identifier
