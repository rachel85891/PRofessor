"""GitHubCodeHost: the CodeHost port over GitHub's REST API, with the standard library only."""

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import SecretStr

from hw_checker.domain import BranchHead, ForkRef, PullRequestRef, RepoTarget
from hw_checker.domain.errors import PublishError

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "hw-checker"
MAX_MESSAGE_CHARS = 300
REDACTED = "***"
HTTP_OK = 200
HTTP_CREATED = 201
HTTP_ACCEPTED = 202
HTTP_NOT_FOUND = 404


@dataclass(frozen=True)
class GitHubConfig:
    """The API, the publishing account and its forks' names, and the waiting limits."""

    api_url: str
    web_url: str
    account: str | None
    fork_prefix: str
    timeout_seconds: float
    fork_wait_seconds: float
    fork_poll_seconds: float


class HttpSender(Protocol):
    """Sends one request and returns its status and body; injectable for tests."""

    def __call__(self, request: urllib.request.Request, timeout: float) -> tuple[int, bytes]: ...


def send_with_urllib(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    """Send `request`, trusting the system certificate store; an HTTP error is a status.

    Raises:
        OSError: the server cannot be reached (`urllib.error.URLError`, a timeout).
    """
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class GitHubCodeHost:
    """Reads default branches, keeps the publishing account's forks and opens pull requests.

    Every request authenticates with the publishing account's token, which is sent only in
    the `Authorization` header and never appears in a log record or an error message.
    """

    def __init__(
        self,
        config: GitHubConfig,
        token: SecretStr | None,
        send: HttpSender = send_with_urllib,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._token = token
        self._send = send
        self._sleep = sleep
        self._clock = clock

    def default_branch_head(self, repo: RepoTarget) -> BranchHead:
        """Return `repo`'s default branch and its commit now.

        Raises:
            PublishError: GitHub cannot be reached, or the repo or branch cannot be read.
        """
        base = _repo_path(repo.owner, repo.name)
        branch = _text(self._request("GET", base), "default_branch")
        head = self._request("GET", f"{base}/branches/{_segment(branch)}")
        return BranchHead(name=branch, sha=_text(head, "commit", "sha"))

    def ensure_fork(self, repo: RepoTarget) -> ForkRef:
        """Fork `repo` into the publishing account as `<fork_prefix><owner>` (or reuse the
        existing fork, whose name GitHub keeps) and wait until the fork answers.

        Raises:
            PublishError: the account is not configured, the fork cannot be created, it
                belongs to another account, or it is not ready in time.
        """
        account = self._account()
        created = self._request(
            "POST",
            f"{_repo_path(repo.owner, repo.name)}/forks",
            {"name": f"{self._config.fork_prefix}{repo.owner}", "default_branch_only": True},
            accepted=(HTTP_OK, HTTP_ACCEPTED),
        )
        owner, name = _text(created, "owner", "login"), _text(created, "name")
        if owner.casefold() != account.casefold():
            raise PublishError(
                f"The fork of {repo.owner}/{repo.name} was made under {owner}, not the "
                f"publishing account {account}; check that the token belongs to {account}."
            )
        self._wait_for(account, name)
        return ForkRef(
            owner=account,
            name=name,
            push_url=f"{self._config.web_url.rstrip('/')}/{account}/{name}.git",
        )

    def open_or_update_pull_request(
        self,
        repo: RepoTarget,
        fork: ForkRef,
        branch_name: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> PullRequestRef:
        """Open the pull request `<fork owner>:<branch_name>` → `base_branch` on `repo`, or
        update the open one from that branch. Maintainers may edit the branch.

        Raises:
            PublishError: the pull request cannot be listed, opened or updated.
        """
        pulls = f"{_repo_path(repo.owner, repo.name)}/pulls"
        head = f"{fork.owner}:{branch_name}"
        query = urllib.parse.urlencode({"state": "open", "head": head})
        found = self._request("GET", f"{pulls}?{query}")
        if not isinstance(found, list):
            raise PublishError(f"GitHub's list of pull requests for {head} is not a list.")
        if found:
            number = _number(found[0], "number")
            updated = self._request("PATCH", f"{pulls}/{number}", {"title": title, "body": body})
            return PullRequestRef(number=number, url=_text(updated, "html_url"), created=False)
        opened = self._request(
            "POST",
            pulls,
            {
                "title": title,
                "body": body,
                "head": head,
                "base": base_branch,
                "maintainer_can_modify": True,
            },
            accepted=(HTTP_CREATED,),
        )
        return PullRequestRef(
            number=_number(opened, "number"), url=_text(opened, "html_url"), created=True
        )

    def _account(self) -> str:
        if not self._config.account:
            raise PublishError(
                "The publishing account is not configured; set github.account in config.yaml."
            )
        return self._config.account

    def _wait_for(self, account: str, name: str) -> None:
        deadline = self._clock() + self._config.fork_wait_seconds
        path = _repo_path(account, name)
        while True:
            status, _ = self._exchange("GET", path, None)
            if status == HTTP_OK:
                return
            if status != HTTP_NOT_FOUND:
                raise PublishError(f"GitHub GET {path} failed (HTTP {status}).")
            if self._clock() >= deadline:
                raise PublishError(
                    f"The fork {account}/{name} is not ready after "
                    f"{self._config.fork_wait_seconds:g} s; publish again later."
                )
            self._sleep(self._config.fork_poll_seconds)

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        accepted: tuple[int, ...] = (HTTP_OK,),
    ) -> object:
        status, data = self._exchange(method, path, body)
        if status not in accepted:
            raise PublishError(
                f"GitHub {method} {path} failed (HTTP {status}): {self._message(data)}"
            )
        try:
            return json.loads(data)
        except ValueError as error:
            raise PublishError(
                f"GitHub {method} {path} returned a reply that is not JSON."
            ) from error

    def _exchange(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> tuple[int, bytes]:
        if self._token is None or not self._token.get_secret_value().strip():
            raise PublishError(
                "The GitHub token is not set. Add HW_CHECKER_GITHUB_TOKEN=<token> to .env: the "
                "publishing account's classic token with the public_repo scope only."
            )
        request = urllib.request.Request(
            f"{self._config.api_url.rstrip('/')}{path}",
            method=method,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token.get_secret_value()}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            status, data = self._send(request, self._config.timeout_seconds)
        except OSError as error:
            raise PublishError(
                f"Cannot reach GitHub at {self._config.api_url}: {self._redact(str(error))}"
            ) from error
        logger.debug("GitHub %s %s: HTTP %d", method, path, status)
        return status, data

    def _message(self, data: bytes) -> str:
        """GitHub's own message and its validation errors, short and with the token removed."""
        try:
            reply = json.loads(data)
        except ValueError:
            reply = {}
        if not isinstance(reply, dict):
            reply = {}
        parts = [str(reply.get("message") or "no message")]
        parts += [
            str(error.get("message") or error.get("code"))
            for error in reply.get("errors") or []
            if isinstance(error, dict)
        ]
        return self._redact(" ".join("; ".join(parts).split()))[:MAX_MESSAGE_CHARS]

    def _redact(self, text: str) -> str:
        token = self._token.get_secret_value() if self._token else ""
        return text.replace(token, REDACTED) if token else text


def _field(reply: object, keys: tuple[str, ...]) -> object:
    value = reply
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise PublishError(f"GitHub's reply has no {'.'.join(keys)}.")
        value = value[key]
    return value


def _text(reply: object, *keys: str) -> str:
    value = _field(reply, keys)
    if not isinstance(value, str) or not value:
        raise PublishError(f"GitHub's reply has no {'.'.join(keys)}.")
    return value


def _number(reply: object, *keys: str) -> int:
    value = _field(reply, keys)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PublishError(f"GitHub's reply has no {'.'.join(keys)}.")
    return value


def _repo_path(owner: str, name: str) -> str:
    return f"/repos/{_segment(owner)}/{_segment(name)}"


def _segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")
