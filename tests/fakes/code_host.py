"""In-memory fake for the CodeHost port."""

from collections.abc import Mapping
from dataclasses import dataclass

from hw_checker.domain import BranchHead, ForkRef, PullRequestRef, RepoTarget
from hw_checker.domain.errors import HwCheckerError

ACCOUNT = "review-bot"


@dataclass(frozen=True)
class OpenedPull:
    """One call that opened or updated a pull request."""

    repo_id: str
    head: str
    base: str
    title: str
    body: str
    created: bool


class FakeCodeHost:
    """Answers with fixed default-branch heads and keeps forks and pull requests in memory.

    `heads` maps a repo id to its default branch's head; `errors` maps a repo id to the
    error every call for it raises. Publishing again from the same branch updates the pull
    request opened first.
    """

    def __init__(
        self,
        heads: Mapping[str, BranchHead] | None = None,
        errors: Mapping[str, HwCheckerError] | None = None,
    ) -> None:
        self.heads = dict(heads or {})
        self._errors = dict(errors or {})
        self.forks: list[str] = []
        self.pulls: list[OpenedPull] = []
        self._numbers: dict[tuple[str, str], int] = {}

    def default_branch_head(self, repo: RepoTarget) -> BranchHead:
        self._raise_for(repo)
        return self.heads[repo.repo_id]

    def ensure_fork(self, repo: RepoTarget) -> ForkRef:
        self._raise_for(repo)
        self.forks.append(repo.repo_id)
        name = f"check_{repo.owner}"
        return ForkRef(
            owner=ACCOUNT, name=name, push_url=f"https://github.test/{ACCOUNT}/{name}.git"
        )

    def open_or_update_pull_request(
        self,
        repo: RepoTarget,
        fork: ForkRef,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRef:
        self._raise_for(repo)
        head = f"{fork.owner}:{branch_name}"
        key = (repo.repo_id, head)
        created = key not in self._numbers
        if created:
            self._numbers[key] = len(self._numbers) + 1
        number = self._numbers[key]
        self.pulls.append(OpenedPull(repo.repo_id, head, base_branch, title, body, created))
        return PullRequestRef(
            number=number,
            url=f"https://github.test/{repo.owner}/{repo.name}/pull/{number}",
            created=created,
        )

    def _raise_for(self, repo: RepoTarget) -> None:
        error = self._errors.get(repo.repo_id)
        if error is not None:
            raise error
