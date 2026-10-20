"""LocalGitClient: the GitClient port implemented with the git CLI."""

import os
import re
import shutil
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from hw_checker.adapters.git_local.log_format import LOG_FORMAT, parse_log_output
from hw_checker.adapters.git_local.runner import CommandRunner, GitRunner, run_subprocess
from hw_checker.domain import CommitInfo
from hw_checker.domain.errors import HwCheckerError, RepoUnavailableError

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_REMOTE_HEAD_PREFIX = "origin/"


class LocalGitClient:
    """Clones and works with local copies of repos through the `git` executable.

    Working copies always hold the repo's exact bytes (`core.autocrlf=false`).
    """

    def __init__(
        self,
        executable: str,
        timeout_seconds: float,
        commit_author_name: str,
        commit_author_email: str,
        runner: CommandRunner = run_subprocess,
    ) -> None:
        self._git = GitRunner(executable, timeout_seconds, runner)
        self._author_name = commit_author_name
        self._author_email = commit_author_email

    def clone_or_fetch(self, url: str, destination: Path) -> None:
        """Clone `url` into `destination`, or update an existing clone to the remote head.

        An existing clone is fetched, reset to the remote default branch and cleaned of
        untracked and ignored files, so it matches a fresh clone. A `destination` that is
        not a clone of `url` is left untouched.

        Raises:
            RepoUnavailableError: the repo cannot be cloned or fetched, is empty, or
                `destination` exists but is not a clone of `url`.
            ExecutionEnvironmentError: the git executable is not available.
        """
        _reject_option_like(url, "URL", RepoUnavailableError)
        if destination.exists():
            self._update(url, destination)
        else:
            self._clone(url, destination)

    def head_sha(self, repo_dir: Path) -> str:
        """Return the full sha of the checked-out commit.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
            ExecutionEnvironmentError: the git executable is not available.
        """
        self._require_own_working_copy(repo_dir)
        sha = self._git.run(["rev-parse", "--verify", "HEAD"], repo_dir, RepoUnavailableError)
        sha = sha.strip()
        if not _SHA_PATTERN.match(sha):
            raise RepoUnavailableError(f"{repo_dir}: unexpected commit sha {sha!r}")
        return sha

    def log(self, repo_dir: Path) -> tuple[CommitInfo, ...]:
        """Return the commit history of the checked-out branch, newest first.

        Raises:
            RepoUnavailableError: `repo_dir` is not a readable Git working copy.
            ExecutionEnvironmentError: the git executable is not available.
        """
        self._require_own_working_copy(repo_dir)
        output = self._git.run(
            ["-c", "i18n.logOutputEncoding=UTF-8", "log", "-z", f"--format={LOG_FORMAT}", "HEAD"],
            repo_dir,
            RepoUnavailableError,
        )
        try:
            return parse_log_output(output)
        except ValueError as error:
            raise RepoUnavailableError(f"{repo_dir}: cannot parse git log: {error}") from error

    def _clone(self, url: str, destination: Path) -> None:
        parent = destination.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RepoUnavailableError(f"cannot create {parent}: {error}") from error
        # Clone next to the destination and rename, so a failed clone never blocks a re-run.
        staging = parent / f".{destination.name}.cloning-{uuid.uuid4().hex[:8]}"
        try:
            self._git.run(
                [
                    "clone",
                    "--no-recurse-submodules",
                    "--config",
                    "core.autocrlf=false",
                    "--config",
                    "core.longpaths=true",
                    "--",
                    url,
                    str(staging),
                ],
                parent,
                RepoUnavailableError,
            )
            self._require_commits(staging, url)
            staging.rename(destination)
        except OSError as error:
            _remove_tree(staging)
            raise RepoUnavailableError(f"cannot move clone into {destination}: {error}") from error
        except BaseException:
            _remove_tree(staging)
            raise

    def _update(self, url: str, destination: Path) -> None:
        self._require_own_working_copy(destination)
        self._require_origin(destination, url)
        self._git.run(
            ["fetch", "--prune", "--no-recurse-submodules", "origin"],
            destination,
            RepoUnavailableError,
        )
        branch = self._remote_default_branch(destination, url)
        remote_ref = f"refs/remotes/origin/{branch}"
        self._git.run(
            ["checkout", "--force", "-B", branch, remote_ref], destination, RepoUnavailableError
        )
        self._git.run(["reset", "--hard", remote_ref], destination, RepoUnavailableError)
        self._git.run(["clean", "-ffdx"], destination, RepoUnavailableError)

    def _require_commits(self, repo_dir: Path, url: str) -> None:
        result = self._git.probe(
            ["rev-parse", "--verify", "--quiet", "HEAD"], repo_dir, RepoUnavailableError
        )
        if result.returncode != 0:
            raise RepoUnavailableError(
                f"{url} has no commits yet (the repository is empty), so there is nothing to check."
            )

    def _require_own_working_copy(self, path: Path) -> None:
        # A plain folder inside another repo would otherwise resolve to that outer repo.
        not_a_clone = RepoUnavailableError(
            f"{path} exists but is not a Git working copy of its own. Nothing was deleted; "
            "move or remove it to clone again."
        )
        if not path.is_dir():
            raise not_a_clone
        result = self._git.probe(["rev-parse", "--show-toplevel"], path, RepoUnavailableError)
        if result.returncode != 0 or not _same_path(Path(result.stdout.strip()), path):
            raise not_a_clone

    def _require_origin(self, repo_dir: Path, url: str) -> None:
        result = self._git.probe(
            ["config", "--get", "remote.origin.url"], repo_dir, RepoUnavailableError
        )
        origin = result.stdout.strip()
        if result.returncode != 0 or origin.rstrip("/") != url.rstrip("/"):
            raise RepoUnavailableError(
                f"{repo_dir} is a clone of {origin or 'an unknown remote'}, not {url}. "
                "Nothing was deleted; move or remove it to clone again."
            )

    def _remote_default_branch(self, repo_dir: Path, url: str) -> str:
        result = self._git.probe(
            ["remote", "set-head", "origin", "--auto"], repo_dir, RepoUnavailableError
        )
        if result.returncode != 0:
            raise RepoUnavailableError(
                f"{url}: cannot determine the default branch; the repository may be empty."
            )
        head = self._git.run(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            repo_dir,
            RepoUnavailableError,
        ).strip()
        if not head.startswith(_REMOTE_HEAD_PREFIX):
            raise RepoUnavailableError(f"{url}: unexpected remote default branch {head!r}")
        return head.removeprefix(_REMOTE_HEAD_PREFIX)


def _reject_option_like(value: str, what: str, error: type[HwCheckerError]) -> None:
    if not value.strip() or value.startswith("-"):
        raise error(f"invalid {what}: {value!r}")


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _remove_tree(path: Path) -> None:
    """Delete a directory this client created; Git marks object files read-only on Windows."""
    if not path.exists():
        return

    def make_writable_and_retry(
        function: Callable[[str], object], target: str, _error: BaseException
    ) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onexc=make_writable_and_retry)
