import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.git_local.runner import BASE_CONFIG
from hw_checker.domain.errors import (
    ExecutionEnvironmentError,
    GitOperationError,
    PublishError,
    RepoUnavailableError,
)

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
    assert args[1 : 1 + len(BASE_CONFIG)] == list(BASE_CONFIG)
    return args[1 + len(BASE_CONFIG) :]


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


def test_option_like_branch_name_is_rejected_before_running_git(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok())

    with pytest.raises(GitOperationError, match="invalid branch name"):
        client(runner).create_branch(tmp_path, "--force", "HEAD")

    assert runner.commands == []


def test_commit_without_paths_is_rejected(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok())

    with pytest.raises(GitOperationError, match="no paths given"):
        client(runner).commit(tmp_path, "fix", ())

    assert runner.commands == []


def test_commit_uses_configured_author_per_command(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["diff", "--cached"]:
            return subprocess.CompletedProcess([], 1, "", "")
        return ok("c" * 40 + "\n")

    runner = FakeCommandRunner(respond)

    sha = client(runner).commit(tmp_path, "fix: hw03-r2", ("a.cs",))

    commit = next(command for command in runner.commands if "commit" in command)
    assert "user.name=HW Checker" in commit
    assert "user.email=hw@example.invalid" in commit
    assert commit[-2:] == ["--", "a.cs"]
    assert sha == "c" * 40


def test_push_never_forces(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "check-ref-format":
            return ok("hw-review/hw03\n")
        return ok()

    runner = FakeCommandRunner(respond)

    client(runner).push(tmp_path, "hw-review/hw03", "https://github.com/a/b")

    push = runner.commands[-1]
    assert push == [
        "push",
        "--",
        "https://github.com/a/b",
        "refs/heads/hw-review/hw03:refs/heads/hw-review/hw03",
    ]
    assert not any(arg.startswith("+") or arg in {"--force", "-f"} for arg in push)


def test_push_failure_maps_to_publish_error(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "check-ref-format":
            return ok("fix\n")
        return failed("! [rejected] fix -> fix (non-fast-forward)")

    with pytest.raises(PublishError, match="non-fast-forward"):
        client(FakeCommandRunner(respond)).push(tmp_path, "fix", "https://github.com/a/b")


def test_branch_shorthand_is_not_accepted_as_a_name(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok("main\n"))

    with pytest.raises(GitOperationError, match="invalid branch name"):
        client(runner).create_branch(tmp_path, "@{-1}", "HEAD")
