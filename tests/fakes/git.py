"""In-memory fake for the GitClient port."""

import shutil
from collections.abc import Mapping
from pathlib import Path

from hw_checker.domain import CommitInfo
from hw_checker.domain.errors import GitOperationError, HwCheckerError, RepoUnavailableError

DEFAULT_SHA = "ab" * 20


class FakeGitClient:
    """Clones by copying a local folder (or creating an empty one) and records each call.

    `sources` maps a URL to the folder copied as its working copy; any other URL gets an
    empty folder. `errors` maps a URL to the error its clone raises.
    """

    def __init__(
        self,
        sources: Mapping[str, Path] | None = None,
        errors: Mapping[str, HwCheckerError] | None = None,
        sha: str = DEFAULT_SHA,
    ) -> None:
        self._sources = dict(sources or {})
        self._errors = dict(errors or {})
        self._sha = sha
        self.cloned: list[tuple[str, Path]] = []
        self.pushed: list[tuple[Path, str, str]] = []

    def clone_or_fetch(self, url: str, destination: Path) -> None:
        self.cloned.append((url, destination))
        error = self._errors.get(url)
        if error is not None:
            raise error
        source = self._sources.get(url)
        if source is None:
            destination.mkdir(parents=True, exist_ok=True)
            return
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("bin", "obj"))

    def head_sha(self, repo_dir: Path) -> str:
        if not repo_dir.is_dir():
            raise RepoUnavailableError(f"{repo_dir} is not a working copy")
        return self._sha

    def log(self, repo_dir: Path) -> tuple[CommitInfo, ...]:
        if not repo_dir.is_dir():
            raise RepoUnavailableError(f"{repo_dir} is not a working copy")
        return ()

    def create_branch(self, repo_dir: Path, branch_name: str, start_point: str) -> None:
        if not repo_dir.is_dir():
            raise GitOperationError(f"{repo_dir} is not a working copy")

    def commit(self, repo_dir: Path, message: str, paths: tuple[str, ...]) -> str:
        if not repo_dir.is_dir():
            raise GitOperationError(f"{repo_dir} is not a working copy")
        return self._sha

    def push(self, repo_dir: Path, branch_name: str, remote_url: str) -> None:
        self.pushed.append((repo_dir, branch_name, remote_url))
