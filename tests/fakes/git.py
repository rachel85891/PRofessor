"""In-memory fake for the GitClient port."""

import difflib
import shutil
from collections.abc import Mapping
from pathlib import Path

from hw_checker.domain import CommitInfo
from hw_checker.domain.errors import GitOperationError, HwCheckerError, RepoUnavailableError

DEFAULT_SHA = "ab" * 20
COMMIT_SHA = "cd" * 20


class FakeGitClient:
    """Clones by copying a local folder (or creating an empty one) and records each call.

    `sources` maps a URL to the folder copied as its working copy; any other URL gets an
    empty folder. `errors` maps a URL to the error its clone raises; `push_error` is raised
    by every push.
    """

    def __init__(
        self,
        sources: Mapping[str, Path] | None = None,
        errors: Mapping[str, HwCheckerError] | None = None,
        sha: str = DEFAULT_SHA,
        push_error: HwCheckerError | None = None,
    ) -> None:
        self._sources = dict(sources or {})
        self._errors = dict(errors or {})
        self._sha = sha
        self._push_error = push_error
        self.cloned: list[tuple[str, Path]] = []
        self.pushed: list[tuple[Path, str, str]] = []
        self.branches: list[tuple[Path, str, str]] = []
        self.commits: list[tuple[Path, str, tuple[str, ...]]] = []
        self._snapshots: dict[Path, dict[str, bytes]] = {}

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
        self.branches.append((repo_dir, branch_name, start_point))

    def commit(self, repo_dir: Path, message: str, paths: tuple[str, ...]) -> str:
        if not repo_dir.is_dir():
            raise GitOperationError(f"{repo_dir} is not a working copy")
        self.commits.append((repo_dir, message, paths))
        return COMMIT_SHA

    def clone_local(self, source: Path, destination: Path) -> None:
        """Copy `source` and remember its files, so `discard_changes` and `diff` can use them."""
        if not source.is_dir():
            raise GitOperationError(f"{source} is not a working copy")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        self._snapshots[destination] = _files(destination)

    def discard_changes(self, repo_dir: Path) -> None:
        snapshot = self._snapshots[repo_dir]
        for path in set(_files(repo_dir)) - set(snapshot):
            (repo_dir / path).unlink()
        for path, data in snapshot.items():
            (repo_dir / path).write_bytes(data)

    def diff(self, repo_dir: Path, base: str) -> str:
        snapshot, current = self._snapshots[repo_dir], _files(repo_dir)
        lines: list[str] = []
        for path in sorted(set(snapshot) | set(current)):
            before = snapshot.get(path, b"").decode("utf-8", "replace").splitlines(keepends=True)
            after = current.get(path, b"").decode("utf-8", "replace").splitlines(keepends=True)
            lines += difflib.unified_diff(before, after, f"a/{path}", f"b/{path}")
        return "".join(lines)

    def push(self, repo_dir: Path, branch_name: str, remote_url: str) -> None:
        if self._push_error is not None:
            raise self._push_error
        self.pushed.append((repo_dir, branch_name, remote_url))


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
