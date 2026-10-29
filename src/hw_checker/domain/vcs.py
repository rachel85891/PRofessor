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


class FetchedRepo(DomainModel):
    """A student repo's local working copy, at the commit that is checked."""

    working_copy: Path
    commit_sha: Identifier
