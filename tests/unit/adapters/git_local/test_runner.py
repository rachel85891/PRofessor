import logging
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hw_checker.adapters.git_local.runner import GitRunner, redact
from hw_checker.domain.errors import (
    ExecutionEnvironmentError,
    GitOperationError,
    PublishError,
    RepoUnavailableError,
)

# A deliberately fake credential, used to check that it never reaches the logs.
FAKE_CREDENTIAL_URL = "https://user:secret@github.com/a/b"  # pragma: allowlist secret


@dataclass
class ScriptedRunner:
    """Returns a fixed result (or raises) and records every call."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    raises: BaseException | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(
        self, args: Sequence[str], *, cwd: Path, timeout: float, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append({"args": list(args), "cwd": cwd, "timeout": timeout, "env": dict(env)})
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


def test_run_returns_stdout_and_passes_safe_options(tmp_path: Path) -> None:
    scripted = ScriptedRunner(stdout="abc\n")

    output = GitRunner("git", 42, scripted).run(["rev-parse", "HEAD"], tmp_path, GitOperationError)

    assert output == "abc\n"
    (call,) = scripted.calls
    args = call["args"]
    assert isinstance(args, list)
    assert args[0] == "git"
    assert args[-2:] == ["rev-parse", "HEAD"]
    assert "credential.helper=" in args
    assert "core.autocrlf=false" in args
    assert call["cwd"] == tmp_path
    assert call["timeout"] == 42


def test_environment_disables_prompts(tmp_path: Path) -> None:
    scripted = ScriptedRunner()

    GitRunner("git", 1, scripted).run(["status"], tmp_path, GitOperationError)

    env = scripted.calls[0]["env"]
    assert isinstance(env, dict)
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["GIT_ASKPASS"] == ""
    assert "PATH" in {key.upper() for key in env}


def test_non_zero_exit_raises_the_given_error_with_stderr(tmp_path: Path) -> None:
    scripted = ScriptedRunner(returncode=128, stderr="fatal: repository not found\n")

    with pytest.raises(RepoUnavailableError, match="repository not found"):
        GitRunner("git", 1, scripted).run(["fetch"], tmp_path, RepoUnavailableError)


def test_probe_returns_non_zero_results_without_raising(tmp_path: Path) -> None:
    scripted = ScriptedRunner(returncode=1)

    result = GitRunner("git", 1, scripted).probe(["show-ref"], tmp_path, GitOperationError)

    assert result.returncode == 1


def test_timeout_raises_the_given_error_naming_command_and_timeout(tmp_path: Path) -> None:
    scripted = ScriptedRunner(raises=subprocess.TimeoutExpired(cmd="git", timeout=5))

    with pytest.raises(RepoUnavailableError, match=r"git clone .* timed out after 5 seconds"):
        GitRunner("git", 5, scripted).run(["clone", "--", "x"], tmp_path, RepoUnavailableError)


def test_missing_executable_raises_execution_environment_error(tmp_path: Path) -> None:
    scripted = ScriptedRunner(raises=FileNotFoundError("no such file"))

    with pytest.raises(ExecutionEnvironmentError, match="'gitx' was not found"):
        GitRunner("gitx", 1, scripted).run(["status"], tmp_path, GitOperationError)


def test_real_missing_executable_raises_execution_environment_error(tmp_path: Path) -> None:
    runner = GitRunner(str(tmp_path / "no-such-git.exe"), 5)

    with pytest.raises(ExecutionEnvironmentError):
        runner.run(["--version"], tmp_path, GitOperationError)


def test_missing_cwd_raises_the_given_error_without_running(tmp_path: Path) -> None:
    scripted = ScriptedRunner()

    with pytest.raises(GitOperationError, match="does not exist"):
        GitRunner("git", 1, scripted).run(["status"], tmp_path / "absent", GitOperationError)

    assert scripted.calls == []


def test_redact_hides_url_credentials() -> None:
    assert redact(FAKE_CREDENTIAL_URL) == "https://***@github.com/a/b"
    assert redact("https://github.com/a/b") == "https://github.com/a/b"


def test_logs_command_at_debug_and_failure_at_warning_without_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    scripted = ScriptedRunner(returncode=1, stderr=f"fatal: could not read {FAKE_CREDENTIAL_URL}")

    with (
        caplog.at_level(logging.DEBUG, logger="hw_checker.adapters.git_local.runner"),
        pytest.raises(PublishError),
    ):
        GitRunner("git", 1, scripted).run(
            ["push", "--", FAKE_CREDENTIAL_URL], tmp_path, PublishError
        )

    levels = [record.levelno for record in caplog.records]
    assert levels == [logging.DEBUG, logging.WARNING]
    assert all("secret" not in record.getMessage() for record in caplog.records)
