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
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def head_sha(self, repo_dir: Path) -> str:
        """Return the full sha of the checked-out commit.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def log(self, repo_dir: Path) -> tuple[CommitInfo, ...]:
        """Return the commit history of the checked-out branch, newest first.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def first_parent_history(self, repo_dir: Path) -> tuple[CommitInfo, ...]:
        """Return the checked-out branch's first-parent history, oldest first.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def tree_files(self, repo_dir: Path, sha: str) -> tuple[str, ...]:
        """Return the repo-relative POSIX paths of every file in the commit `sha`, without
        checking it out.

        Raises:
            RepoUnavailableError: `sha` is not a commit of the repo, or the tree cannot be read.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def check_out(self, repo_dir: Path, sha: str) -> None:
        """Check out the commit `sha` (detached), dropping any local change.

        Raises:
            RepoUnavailableError: `sha` is not a commit of the repo, or the checkout failed.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def create_branch(self, repo_dir: Path, branch_name: str, start_point: str) -> None:
        """Create and check out `branch_name` at the commit `start_point`.

        Raises:
            GitOperationError: the branch cannot be created or checked out.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def commit(self, repo_dir: Path, message: str, paths: tuple[str, ...]) -> str:
        """Stage the repo-relative `paths`, commit them with `message`, and return the new sha.

        Raises:
            GitOperationError: staging or committing failed.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def clone_local(self, source: Path, destination: Path) -> None:
        """Clone the local working copy `source` into `destination`, at its checked-out commit.

        A `destination` left by an earlier attempt is replaced; `source` is never changed.

        Raises:
            GitOperationError: the clone failed or `destination` cannot be replaced.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def discard_changes(self, repo_dir: Path) -> None:
        """Drop every uncommitted change and untracked file, back to the checked-out commit.

        Raises:
            GitOperationError: the working copy cannot be reset.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def diff(self, repo_dir: Path, base: str) -> str:
        """Return the unified diff from the commit `base` to the working tree.

        Raises:
            GitOperationError: the diff cannot be made.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...

    def push(self, repo_dir: Path, branch_name: str, remote_url: str) -> None:
        """Push `branch_name` to `remote_url`, creating or updating the remote branch.

        Raises:
            PublishError: the push was rejected or failed.
            ExecutionEnvironmentError: the git executable is not available.
        """
        ...
