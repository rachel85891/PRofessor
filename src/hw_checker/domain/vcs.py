"""Version-control models used by the Git and code-host ports."""

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
