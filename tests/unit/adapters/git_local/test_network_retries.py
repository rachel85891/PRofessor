"""Clone and fetch retry transient network failures, and never permanent ones."""

import logging
import subprocess
from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.git_local.network_errors import transient_failure
from hw_checker.domain.errors import RepoUnavailableError
from tests.unit.adapters.git_local.test_client import FakeCommandRunner, failed, ok

URL = "https://github.com/student/api"
TRANSIENT = [
    ("fatal: unable to access 'x': getaddrinfo() thread failed to start", "name resolution"),
    ("fatal: unable to access 'x': Could not resolve host: github.com", "name resolution"),
    ("fatal: unable to access 'x': Failed to connect to github.com port 443", "connection"),
    ("fatal: unable to access 'x': Connection timed out after 30000 ms", "connection"),
    ("fatal: unable to access 'x': Recv failure: Connection reset by peer", "connection"),
    ("fatal: unable to access 'x': The requested URL returned error: 502", "server error"),
    (
        "fetch-pack: unexpected disconnect while reading sideband packet\nfatal: early EOF",
        "interrupted transfer",
    ),
    ("error: RPC failed; curl 92 HTTP/2 stream 0 was not closed cleanly", "interrupted transfer"),
]
PERMANENT = [
    "remote: Repository not found.\nfatal: repository 'x' not found",
    "fatal: unable to access 'x': The requested URL returned error: 404",
    "fatal: unable to access 'x': The requested URL returned error: 403",
    "fatal: Authentication failed for 'x'",
    "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
]


class Cloner:
    """A fake git whose clone fails with `stderr` for the first `failures` attempts.

    Each attempt first creates its staging folder, as a real clone that dies halfway does,
    and checks that no earlier attempt left one behind.
    """

    def __init__(self, parent: Path, stderr: str, failures: int) -> None:
        self.parent = parent
        self.stderr = stderr
        self.failures = failures
        self.attempts = 0
        self.leftovers: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[0] != "clone":
            return ok()
        self.leftovers.append(sorted(p.name for p in self.parent.glob(".*.cloning-*")))
        self.attempts += 1
        Path(args[-1]).mkdir()
        if self.attempts <= self.failures:
            return failed(self.stderr)
        return ok()


def git(responder: Cloner, sleeps: list[float], retries: int = 2) -> LocalGitClient:
    return LocalGitClient(
        "git",
        10,
        None,
        None,
        FakeCommandRunner(responder),
        network_retries=retries,
        retry_backoff_seconds=3,
        sleep=sleeps.append,
    )


@pytest.mark.parametrize(("stderr", "reason"), TRANSIENT)
def test_transient_failure_is_retried_then_succeeds(
    tmp_path: Path, stderr: str, reason: str
) -> None:
    cloner = Cloner(tmp_path, stderr, failures=1)
    sleeps: list[float] = []

    git(cloner, sleeps).clone_or_fetch(URL, tmp_path / "student__api")

    assert cloner.attempts == 2
    assert sleeps == [3]
    assert (tmp_path / "student__api").is_dir()
    assert transient_failure(stderr) == reason


def test_gives_up_after_the_configured_retries(tmp_path: Path) -> None:
    cloner = Cloner(tmp_path, TRANSIENT[0][0], failures=99)
    sleeps: list[float] = []

    with pytest.raises(RepoUnavailableError, match="getaddrinfo"):
        git(cloner, sleeps, retries=2).clone_or_fetch(URL, tmp_path / "student__api")

    assert cloner.attempts == 3
    assert sleeps == [3, 3]


def test_staging_folder_is_removed_after_every_failed_attempt(tmp_path: Path) -> None:
    cloner = Cloner(tmp_path, TRANSIENT[2][0], failures=99)

    with pytest.raises(RepoUnavailableError):
        git(cloner, []).clone_or_fetch(URL, tmp_path / "student__api")

    assert cloner.leftovers == [[], [], []]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("stderr", PERMANENT)
def test_missing_repo_or_denied_access_is_not_retried(tmp_path: Path, stderr: str) -> None:
    cloner = Cloner(tmp_path, stderr, failures=99)
    sleeps: list[float] = []

    with pytest.raises(RepoUnavailableError):
        git(cloner, sleeps).clone_or_fetch(URL, tmp_path / "student__api")

    assert (cloner.attempts, sleeps) == (1, [])
    assert transient_failure(stderr) is None


def test_zero_retries_tries_once(tmp_path: Path) -> None:
    cloner = Cloner(tmp_path, TRANSIENT[0][0], failures=99)

    with pytest.raises(RepoUnavailableError):
        git(cloner, [], retries=0).clone_or_fetch(URL, tmp_path / "student__api")

    assert cloner.attempts == 1


def test_retry_is_logged_with_the_repo_id_and_reason_never_the_url(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret_url = "https://student:ghp_secret@github.com/student/api"  # pragma: allowlist secret
    cloner = Cloner(tmp_path, TRANSIENT[1][0], failures=1)

    with caplog.at_level(logging.WARNING, logger="hw_checker.adapters.git_local.client"):
        git(cloner, []).clone_or_fetch(secret_url, tmp_path / "student__api")

    (record,) = [r for r in caplog.records if r.name == "hw_checker.adapters.git_local.client"]
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "student__api" in message and "name resolution" in message
    assert "ghp_secret" not in message and "github.com" not in message


def test_fetch_of_an_existing_clone_is_retried(tmp_path: Path) -> None:
    destination = tmp_path / "student__api"
    destination.mkdir()
    fetches: list[int] = []

    def respond(args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return ok(str(destination))
        if args[:3] == ["config", "--get", "remote.origin.url"]:
            return ok(URL)
        if args[0] == "fetch":
            fetches.append(1)
            return failed(TRANSIENT[4][0]) if len(fetches) == 1 else ok()
        if args[0] == "symbolic-ref":
            return ok("origin/main")
        return ok()

    sleeps: list[float] = []
    client = LocalGitClient("git", 10, None, None, FakeCommandRunner(respond), sleep=sleeps.append)

    client.clone_or_fetch(URL, destination)

    assert (len(fetches), sleeps) == (2, [3.0])


def test_negative_settings_are_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        LocalGitClient("git", 10, None, None, network_retries=-1)
