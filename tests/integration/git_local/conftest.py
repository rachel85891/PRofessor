"""Fixtures for LocalGitClient integration tests: isolated git config and local remotes."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient

AUTHOR_NAME = "HW Checker Test"
AUTHOR_EMAIL = "hw-checker-test@example.invalid"


def git(*args: str, cwd: Path) -> str:
    """Run real git for test setup; fail the test on error."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


class Remote:
    """A bare repo used as the remote, plus a working copy that pushes commits to it."""

    def __init__(self, root: Path, name: str = "remote") -> None:
        self.bare = root / f"{name}.git"
        self.author = root / f"{name}-author"
        git("init", "--bare", "--initial-branch=main", str(self.bare), cwd=root)
        git("clone", str(self.bare), str(self.author), cwd=root)
        git("config", "core.autocrlf", "false", cwd=self.author)

    @property
    def url(self) -> str:
        return self.bare.as_uri()

    def commit(self, files: dict[str, bytes], message: str, branch: str = "main") -> str:
        git("checkout", "-B", branch, cwd=self.author)
        for name, content in files.items():
            path = self.author / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            git("add", "--", name, cwd=self.author)
        git("commit", "-m", message, cwd=self.author)
        git("push", "origin", f"{branch}:{branch}", cwd=self.author)
        return git("rev-parse", "HEAD", cwd=self.author).strip()

    def set_default_branch(self, branch: str) -> None:
        git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=self.bare)

    def branch_sha(self, branch: str) -> str:
        return git("rev-parse", f"refs/heads/{branch}", cwd=self.bare).strip()


@pytest.fixture(autouse=True)
def isolated_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ignore the machine's git config and simulate a global `core.autocrlf=true`."""
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        "[core]\n\tautocrlf = true\n"
        f"[user]\n\tname = {AUTHOR_NAME}\n\temail = {AUTHOR_EMAIL}\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    yield


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    return Remote(tmp_path)


@pytest.fixture
def client() -> LocalGitClient:
    return LocalGitClient("git", 60, "HW Checker Bot", "bot@example.invalid")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "workspace"
    path.mkdir()
    return path
