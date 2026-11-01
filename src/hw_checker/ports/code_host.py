"""Port for the code host: student repos' default branches, review forks, pull requests."""

from typing import Protocol

from hw_checker.domain import BranchHead, ForkRef, PullRequestRef, RepoTarget


class CodeHost(Protocol):
    """Reads default branches, keeps the publishing account's forks and opens pull requests.

    Knows nothing about cloning or pushing (docs/fix-pr-workflow.md §1, §2.6).
    """

    def default_branch_head(self, repo: RepoTarget) -> BranchHead:
        """Return `repo`'s default branch and the commit it points to now.

        Raises:
            PublishError: the code host cannot be reached or the repo cannot be read.
        """
        ...

    def ensure_fork(self, repo: RepoTarget) -> ForkRef:
        """Return the publishing account's fork of `repo`, creating it if needed.

        The fork is reused across rounds; it is ready to receive a push when this returns.

        Raises:
            PublishError: the fork cannot be created, or is not ready in time.
        """
        ...

    def open_or_update_pull_request(
        self,
        repo: RepoTarget,
        fork: ForkRef,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRef:
        """Open a pull request on `repo` from `branch_name` in `fork` into `base_branch`.

        If a pull request from that branch is already open, update its title and body
        instead, so publishing again never opens a second one.

        Raises:
            PublishError: the pull request cannot be opened or updated.
        """
        ...
