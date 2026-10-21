"""Runs git commands safely: argument lists, explicit cwd, timeout, no interactive prompts."""

import logging
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from hw_checker.domain.errors import ExecutionEnvironmentError, HwCheckerError

logger = logging.getLogger(__name__)

MAX_LOGGED_STDERR_CHARS = 500

# Applied to every command. The empty credential.helper disables every configured helper,
# including Git Credential Manager, so no credential dialog can open.
BASE_CONFIG = (
    "-c",
    "credential.helper=",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.longpaths=true",
)

# Environment overrides for every command. With prompts disabled, a private or missing repo
# fails at once instead of waiting for input. An empty GIT_ASKPASS also bypasses core.askPass.
PROMPT_FREE_ENVIRONMENT = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
}

_URL_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")


class CommandRunner(Protocol):
    """Runs one process; injectable so tests can replace `subprocess.run`."""

    def __call__(
        self, args: Sequence[str], *, cwd: Path, timeout: float, env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]: ...


def run_subprocess(
    args: Sequence[str], *, cwd: Path, timeout: float, env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run `args` without a shell, capturing UTF-8 output (undecodable bytes replaced)."""
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=dict(env),
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        check=False,
    )


def redact(text: str) -> str:
    """Hide credentials in the user-info part of URLs before logging."""
    return _URL_CREDENTIALS.sub("***@", text)


class GitRunner:
    """Runs git with the configured executable and timeout, mapping failures to domain errors."""

    def __init__(
        self,
        executable: str,
        timeout_seconds: float,
        runner: CommandRunner = run_subprocess,
        ssl_backend: str | None = None,
    ) -> None:
        self._executable = executable
        self._timeout = timeout_seconds
        self._runner = runner
        # None leaves the TLS backend to the machine's git config.
        tls_config = () if ssl_backend is None else ("-c", f"http.sslBackend={ssl_backend}")
        self._config = (*BASE_CONFIG, *tls_config)

    def run(self, args: Sequence[str], cwd: Path, error: type[HwCheckerError]) -> str:
        """Run `git <args>` in `cwd` and return stdout; raise `error` on a non-zero exit.

        Raises:
            ExecutionEnvironmentError: the git executable cannot be started.
            error: git failed, timed out, or `cwd` does not exist.
        """
        result = self.probe(args, cwd, error)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            logger.warning(
                "git %s failed with exit code %d: %s",
                redact(" ".join(args)),
                result.returncode,
                redact(stderr[:MAX_LOGGED_STDERR_CHARS]),
            )
            raise error(f"git {redact(' '.join(args))} failed: {redact(stderr) or 'no output'}")
        return result.stdout

    def probe(
        self, args: Sequence[str], cwd: Path, error: type[HwCheckerError]
    ) -> subprocess.CompletedProcess[str]:
        """Run `git <args>` in `cwd` and return the result, whatever the exit code.

        Raises:
            ExecutionEnvironmentError: the git executable cannot be started.
            error: git timed out, or `cwd` does not exist.
        """
        command = redact(" ".join(args))
        if not cwd.is_dir():
            raise error(f"git {command}: directory {cwd} does not exist")
        logger.debug("git %s", command, extra={"cwd": str(cwd)})
        try:
            return self._runner(
                [self._executable, *self._config, *args],
                cwd=cwd,
                timeout=self._timeout,
                env={**os.environ, **PROMPT_FREE_ENVIRONMENT},
            )
        except FileNotFoundError as exc:
            raise ExecutionEnvironmentError(
                f"git executable '{self._executable}' was not found. Install Git for Windows "
                "or set git.executable in config.yaml."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            logger.warning("git %s timed out after %s seconds", command, self._timeout)
            raise error(f"git {command} timed out after {self._timeout} seconds") from exc
