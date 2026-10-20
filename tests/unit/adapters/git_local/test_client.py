import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.domain.errors import ExecutionEnvironmentError, RepoUnavailableError

Responder = Callable[[list[str]], subprocess.CompletedProcess[str]]


class FakeCommandRunner:
    """Answers each git call through `respond` (keyed on the git subcommand args)."""

    def __init__(self, respond: Responder) -> None:
        self._respond = respond
        self.commands: list[list[str]] = []

    def __call__(
        self, args: Sequence[str], *, cwd: Path, timeout: float, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        git_args = _strip_base_config(list(args))
        self.commands.append(git_args)
        return self._respond(git_args)


def _strip_base_config(args: list[str]) -> list[str]:
    rest = args[1:]
    while rest[:1] == ["-c"] and not rest[1].startswith("i18n."):
        rest = rest[2:]
    return rest


def ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout, "")


def failed(stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 128, "", stderr)


def client(runner: FakeCommandRunner) -> LocalGitClient:
    return LocalGitClient("git", 10, "HW Checker", "hw@example.invalid", runner)


def test_option_like_url_is_rejected_before_running_git(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok())

    with pytest.raises(RepoUnavailableError, match="invalid URL"):
        client(runner).clone_or_fetch("--upload-pack=evil", tmp_path / "repo")

    assert runner.commands == []


def test_clone_passes_url_after_double_dash(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok())

    with pytest.raises(RepoUnavailableError):
        # The fake clone creates nothing, so moving it into place fails afterwards.
        client(runner).clone_or_fetch("https://github.com/a/b", tmp_path / "repo")

    clone = runner.commands[0]
    assert clone[0] == "clone"
    assert clone[clone.index("--") + 1] == "https://github.com/a/b"
    assert "--no-recurse-submodules" in clone
    assert "core.autocrlf=false" in clone


def test_failed_clone_maps_to_repo_unavailable(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: failed("fatal: repository not found"))

    with pytest.raises(RepoUnavailableError, match="repository not found"):
        client(runner).clone_or_fetch("https://github.com/a/b", tmp_path / "repo")

    assert list(tmp_path.iterdir()) == []


def test_head_sha_rejects_unexpected_output(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(str(tmp_path))
        return ok("not-a-sha\n")

    with pytest.raises(RepoUnavailableError, match="unexpected commit sha"):
        client(FakeCommandRunner(respond)).head_sha(tmp_path)


def test_head_sha_in_a_folder_of_another_repo_is_rejected(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok(str(tmp_path.parent)))

    with pytest.raises(RepoUnavailableError, match="not a Git working copy"):
        client(runner).head_sha(tmp_path)


def test_log_with_unparsable_output_maps_to_repo_unavailable(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(str(tmp_path))
        return ok("garbage\0")

    with pytest.raises(RepoUnavailableError, match="cannot parse git log"):
        client(FakeCommandRunner(respond)).log(tmp_path)


def test_timeout_maps_to_repo_unavailable(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="git", timeout=10)

    with pytest.raises(RepoUnavailableError, match="timed out after 10 seconds"):
        client(FakeCommandRunner(respond)).clone_or_fetch("https://github.com/a/b", tmp_path / "r")


def test_missing_executable_maps_to_execution_environment_error(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("git")

    with pytest.raises(ExecutionEnvironmentError):
        client(FakeCommandRunner(respond)).head_sha(tmp_path)
