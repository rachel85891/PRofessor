"""Port for local Git operations on student repos."""

from pathlib import Path
from typing import Protocol

from hw_checker.domain import CommitInfo


class GitClient(Protocol):
    """Clones and works with local copies of repos. Knows nothing about pull requests."""

    def clone_or_fetch(self, url: str, destination: Path) -> None:
        """Clone `url` into `destination`, or fetch and reset to the remote head if it exists.

        Raises:
            RepoUnavailableError: the repo cannot be cloned or fetched.
        """
        ...

    def head_sha(self, repo_dir: Path) -> str:
        """Return the full sha of the checked-out commit.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
        """
        ...

    def log(self, repo_dir: Path) -> tuple[CommitInfo, ...]:
        """Return the commit history of the checked-out branch, newest first.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
        """
        ...

    def create_branch(self, repo_dir: Path, branch_name: str, start_point: str) -> None:
        """Create and check out `branch_name` at the commit `start_point`.

        Raises:
            GitOperationError: the branch cannot be created or checked out.
        """
        ...

    def commit(self, repo_dir: Path, message: str, paths: tuple[str, ...]) -> str:
        """Stage the repo-relative `paths`, commit them with `message`, and return the new sha.

        Raises:
            GitOperationError: staging or committing failed.
        """
        ...

    def push(self, repo_dir: Path, branch_name: str, remote_url: str) -> None:
        """Push `branch_name` to `remote_url`, creating or updating the remote branch.

        Raises:
            PublishError: the push was rejected or failed.
        """
        ...
