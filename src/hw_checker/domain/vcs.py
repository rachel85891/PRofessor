"""Version-control models used by the Git and code-host ports."""

from enum import StrEnum
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


class CommitChoice(StrEnum):
    """Why the round checks the commit it checks, for an assignment with `ends_when`."""

    BEFORE_NEXT_TASK = "before_next_task"
    """The last commit before the first one where the next task had started."""
    NEXT_TASK_NOT_STARTED = "next_task_not_started"
    """The next task never started, so the latest commit is checked."""
    NEXT_TASK_AT_FIRST_COMMIT = "next_task_at_first_commit"
    """The condition already holds at the repo's first commit, so it cannot tell the tasks
    apart; the latest commit is checked."""


class CheckedCommit(DomainModel):
    """Which commit of the repo's default branch the round checks, and why.

    `next_task_sha` is the first commit where the next task had started (None when it never
    did); `later_commits` counts the first-parent commits after the checked one, up to
    `latest_sha`, the branch head when the repo was fetched.
    """

    sha: Identifier
    authored_at: AwareDatetime
    choice: CommitChoice
    next_task_sha: Identifier | None = None
    later_commits: int = Field(default=0, ge=0)
    latest_sha: Identifier


class FetchedRepo(DomainModel):
    """A student repo's local working copy, at the commit that is checked.

    `checked` says how that commit was chosen; None when the assignment has no `ends_when`
    and the branch head is checked.
    """

    working_copy: Path
    commit_sha: Identifier
    checked: CheckedCommit | None = None

    @property
    def latest_sha(self) -> str:
        """The branch head when the repo was fetched; publishing compares against it."""
        return self.commit_sha if self.checked is None else self.checked.latest_sha
