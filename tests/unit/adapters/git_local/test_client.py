import logging
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
from pydantic import SecretStr

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.git_local.client import CREDENTIAL_HELPER, PUSH_TOKEN_VARIABLE
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
        self.envs: list[Mapping[str, str]] = []

    def __call__(
        self, args: Sequence[str], *, cwd: Path, timeout: float, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        git_args = _strip_base_config(list(args))
        self.commands.append(git_args)
        self.envs.append(env)
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


PUSH_TOKEN = "ghp_not-a-real-token-1111"  # pragma: allowlist secret


def branch_ok(args: list[str]) -> subprocess.CompletedProcess[str]:
    return ok("hw-review/hw03\n") if args[0] == "check-ref-format" else ok()


def test_push_with_a_token_passes_it_only_in_that_commands_environment(tmp_path: Path) -> None:
    runner = FakeCommandRunner(branch_ok)
    git = LocalGitClient(
        "git", 10, "HW Checker", "hw@example.invalid", runner, push_token=SecretStr(PUSH_TOKEN)
    )

    git.push(tmp_path, "hw-review/hw03", "https://github.com/a/b.git")

    push = runner.commands[-1]
    assert push[:2] == ["-c", f"credential.helper={CREDENTIAL_HELPER}"]
    assert push[2:4] == ["push", "--"]
    assert runner.envs[-1][PUSH_TOKEN_VARIABLE] == PUSH_TOKEN
    assert all(PUSH_TOKEN not in arg for command in runner.commands for arg in command)
    assert all(PUSH_TOKEN_VARIABLE not in env for env in runner.envs[:-1])
    assert "config" not in [command[0] for command in runner.commands]


def test_a_failed_push_never_shows_the_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] == "check-ref-format":
            return ok("fix\n")
        return failed(f"remote: Invalid username or password {PUSH_TOKEN}")

    git = LocalGitClient(
        "git",
        10,
        "HW Checker",
        "hw@example.invalid",
        FakeCommandRunner(respond),
        push_token=SecretStr(PUSH_TOKEN),
    )

    with caplog.at_level(logging.DEBUG), pytest.raises(PublishError) as raised:
        git.push(tmp_path, "fix", "https://github.com/a/b.git")

    assert PUSH_TOKEN not in str(raised.value)
    assert "Invalid username or password ***" in str(raised.value)
    assert all(PUSH_TOKEN not in record.getMessage() for record in caplog.records)


def test_clone_local_copies_files_without_hard_links(tmp_path: Path) -> None:
    source, destination = tmp_path / "checked", tmp_path / "fixes" / "repo"
    source.mkdir()
    (tmp_path / "fixes").mkdir()
    destination.mkdir()
    (destination / "old.cs").write_text("left by an earlier attempt")

    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        return ok(f"{source}\n") if args[:2] == ["rev-parse", "--show-toplevel"] else ok()

    runner = FakeCommandRunner(respond)

    client(runner).clone_local(source, destination)

    clone = runner.commands[-1]
    assert clone[0] == "clone" and "--no-hardlinks" in clone
    assert clone[-3:] == ["--", str(source), str(destination)]
    assert not destination.exists()


def test_discard_changes_resets_and_cleans(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok())

    client(runner).discard_changes(tmp_path)

    assert runner.commands == [["reset", "--quiet", "--hard", "HEAD"], ["clean", "-ffdxq"]]


def test_diff_compares_the_base_commit_with_the_working_tree(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok("diff --git a/x b/x\n"))

    diff = client(runner).diff(tmp_path, "a" * 40)

    assert diff == "diff --git a/x b/x\n"
    assert runner.commands == [
        ["-c", "core.quotepath=false", "diff", "--no-color", "--no-ext-diff", "a" * 40, "--"]
    ]


def test_diff_refuses_an_option_as_the_base(tmp_path: Path) -> None:
    with pytest.raises(GitOperationError, match="invalid base commit"):
        client(FakeCommandRunner(lambda args: ok())).diff(tmp_path, "--output=x")


def test_branch_shorthand_is_not_accepted_as_a_name(tmp_path: Path) -> None:
    runner = FakeCommandRunner(lambda args: ok("main\n"))

    with pytest.raises(GitOperationError, match="invalid branch name"):
        client(runner).create_branch(tmp_path, "@{-1}", "HEAD")


@pytest.mark.parametrize(
    ("name", "email"), [(None, "hw@example.invalid"), ("HW Checker", None), (None, None)]
)
def test_commit_without_configured_author_runs_no_git(
    tmp_path: Path, name: str | None, email: str | None
) -> None:
    runner = FakeCommandRunner(lambda args: ok())
    unconfigured = LocalGitClient("git", 10, name, email, runner)

    with pytest.raises(GitOperationError, match="commit author is not configured"):
        unconfigured.commit(tmp_path, "fix", ("a.cs",))

    assert runner.commands == []


def test_commit_disables_signing(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["diff", "--cached"]:
            return subprocess.CompletedProcess([], 1, "", "")
        return ok("c" * 40 + "\n")

    runner = FakeCommandRunner(respond)

    client(runner).commit(tmp_path, "fix", ("a.cs",))

    commit = next(command for command in runner.commands if "commit" in command)
    position = commit.index("commit.gpgsign=false")
    assert commit[position - 1] == "-c"
    assert position < commit.index("commit")


def test_log_works_without_configured_author(tmp_path: Path) -> None:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(str(tmp_path))
        return ok("")

    unconfigured = LocalGitClient("git", 10, None, None, FakeCommandRunner(respond))

    assert unconfigured.log(tmp_path) == ()


SHA = "a" * 40


def own_copy(tmp_path: Path, output: str = "") -> FakeCommandRunner:
    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(str(tmp_path))
        return ok(output)

    return FakeCommandRunner(respond)


def test_first_parent_history_asks_git_for_the_main_line_oldest_first(tmp_path: Path) -> None:
    runner = own_copy(tmp_path)

    assert client(runner).first_parent_history(tmp_path) == ()

    log = runner.commands[-1]
    assert log[log.index("log") :][:4] == ["log", "-z", "--first-parent", "--reverse"]


def test_tree_files_lists_a_commit_without_checking_it_out(tmp_path: Path) -> None:
    runner = own_copy(tmp_path, "Api/Api.csproj\0Entities/User.cs\0")

    files = client(runner).tree_files(tmp_path, SHA)

    assert files == ("Api/Api.csproj", "Entities/User.cs")
    assert runner.commands == [["ls-tree", "-r", "-z", "--name-only", SHA]]


def test_check_out_detaches_at_the_commit_and_cleans(tmp_path: Path) -> None:
    runner = own_copy(tmp_path)

    client(runner).check_out(tmp_path, SHA)

    assert ["checkout", "--quiet", "--force", "--detach", SHA] in runner.commands
    assert runner.commands[-1] == ["clean", "-ffdxq"]


@pytest.mark.parametrize("bad", ["HEAD~1", "--orphan", "abc123"])
def test_tree_files_and_check_out_take_full_shas_only(tmp_path: Path, bad: str) -> None:
    runner = own_copy(tmp_path)

    with pytest.raises(RepoUnavailableError, match="not a full commit sha"):
        client(runner).tree_files(tmp_path, bad)
    with pytest.raises(RepoUnavailableError, match="not a full commit sha"):
        client(runner).check_out(tmp_path, bad)
    assert runner.commands == []
