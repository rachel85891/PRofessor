"""Port for the code host (pull requests and permissions)."""

from typing import Protocol

from hw_checker.domain import PullRequestRef, RepoTarget


class CodeHost(Protocol):
    """Opens pull requests and checks permissions. Knows nothing about cloning."""

    def has_push_access(self, repo: RepoTarget) -> bool:
        """Return whether the configured account may push branches to `repo`.

        Raises:
            PublishError: the code host cannot be reached or the repo cannot be queried.
        """
        ...

    def open_or_update_pull_request(
        self, repo: RepoTarget, branch_name: str, title: str, body: str
    ) -> PullRequestRef:
        """Open a pull request from `branch_name` in `repo`, or update the open one.

        Publishing again for the same branch updates the existing pull request.

        Raises:
            PublishError: the pull request cannot be opened or updated.
        """
        ...
