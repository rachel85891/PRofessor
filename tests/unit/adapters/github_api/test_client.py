import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import SecretStr

from hw_checker.adapters.github_api import GitHubCodeHost, GitHubConfig
from hw_checker.domain import BranchHead, ForkRef
from hw_checker.domain.errors import PublishError
from tests.unit.domain import builders

FAKE_TOKEN = "ghp_not-a-real-token-0000"  # pragma: allowlist secret
ACCOUNT = "review-bot"
REPO = builders.repo_target()  # dana/api
FORK = ForkRef(owner=ACCOUNT, name="check_dana", push_url="https://github.com/x/y.git")
SOURCE = {"name": "api", "owner": {"login": "dana"}}
OTHER_REPO = builders.repo_target(
    repo_id="student-a-webapi-hw",
    owner="student-a",
    name="webapi-hw",
    url="https://github.com/student-a/webapi-hw",
)

Reply = tuple[int, Any]
Handler = Callable[[str, str, Any], Reply]


class FakeGitHub:
    """Answers each request through `handler(method, path, body)` and records it."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
        self.requests.append(request)
        path = request.full_url.removeprefix("https://api.github.test")
        body = json.loads(request.data) if isinstance(request.data, bytes) else None
        status, reply = self._handler(request.get_method(), path, body)
        return status, reply if isinstance(reply, bytes) else json.dumps(reply).encode()

    def calls(self) -> list[tuple[str, str]]:
        return [
            (r.get_method(), r.full_url.removeprefix("https://api.github.test"))
            for r in self.requests
        ]


def sent_json(request: urllib.request.Request) -> Any:
    assert isinstance(request.data, bytes)
    return json.loads(request.data)


def host(
    handler: Handler, *, token: str | None = FAKE_TOKEN, account: str | None = ACCOUNT
) -> tuple[GitHubCodeHost, FakeGitHub, list[float]]:
    sender = FakeGitHub(handler)
    sleeps: list[float] = []
    clock = iter(range(1000))
    config = GitHubConfig(
        api_url="https://api.github.test",
        web_url="https://github.test",
        account=account,
        fork_prefix="check_",
        timeout_seconds=5,
        fork_wait_seconds=3,
        fork_poll_seconds=1,
    )
    code_host = GitHubCodeHost(
        config,
        None if token is None else SecretStr(token),
        send=sender,
        sleep=sleeps.append,
        clock=lambda: float(next(clock)),
    )
    return code_host, sender, sleeps


def test_default_branch_head_reads_the_branch_and_its_commit() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if path == "/repos/dana/api":
            return 200, {"default_branch": "master"}
        return 200, {"commit": {"sha": "a" * 40}}

    code_host, sender, _ = host(handler)

    assert code_host.default_branch_head(REPO) == BranchHead(name="master", sha="a" * 40)
    assert sender.calls() == [
        ("GET", "/repos/dana/api"),
        ("GET", "/repos/dana/api/branches/master"),
    ]


def test_every_request_carries_the_token_only_in_the_authorization_header() -> None:
    code_host, sender, _ = host(
        lambda m, p, b: (200, {"default_branch": "main", "commit": {"sha": "b" * 40}})
    )

    code_host.default_branch_head(REPO)

    for request in sender.requests:
        assert request.get_header("Authorization") == f"Bearer {FAKE_TOKEN}"
        assert FAKE_TOKEN not in request.full_url
        assert request.get_header("X-github-api-version") == "2022-11-28"


def test_ensure_fork_names_it_after_the_owner_and_waits_until_it_answers() -> None:
    answers = iter([404, 404, 200])

    def handler(method: str, path: str, body: Any) -> Reply:
        if path == "/repos/dana/api":
            return 200, SOURCE
        if method == "POST":
            return 202, {"name": "check_dana", "owner": {"login": ACCOUNT}}
        return next(answers), {}

    code_host, sender, sleeps = host(handler)

    fork = code_host.ensure_fork(REPO)

    assert fork == ForkRef(
        owner=ACCOUNT, name="check_dana", push_url="https://github.test/review-bot/check_dana.git"
    )
    assert sender.calls()[:2] == [("GET", "/repos/dana/api"), ("POST", "/repos/dana/api/forks")]
    assert sent_json(sender.requests[1]) == {
        "name": "check_dana",
        "default_branch_only": True,
    }
    assert sender.calls()[2:] == [("GET", "/repos/review-bot/check_dana")] * 3
    assert sleeps == [1, 1]


def test_ensure_fork_uses_the_name_github_kept_for_an_existing_fork() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if path == "/repos/dana/api":
            return 200, SOURCE
        if method == "POST":
            return 202, {"name": "api", "owner": {"login": "Review-Bot"}}
        return 200, {}

    code_host, sender, _ = host(handler)

    assert code_host.ensure_fork(REPO).name == "api"
    assert [call for call in sender.calls() if call[0] == "POST"] == [
        ("POST", "/repos/dana/api/forks")
    ]


def test_ensure_fork_refuses_a_fork_under_another_account() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if method == "GET":
            return 200, SOURCE
        return 202, {"name": "check_dana", "owner": {"login": "me"}}

    code_host, _, _ = host(handler)

    with pytest.raises(PublishError, match="made under me, not the publishing account review-bot"):
        code_host.ensure_fork(REPO)


def test_ensure_fork_gives_up_when_the_fork_is_not_ready_in_time() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if path == "/repos/dana/api":
            return 200, SOURCE
        if method == "POST":
            return 202, {"name": "check_dana", "owner": {"login": ACCOUNT}}
        return 404, {"message": "Not Found"}

    code_host, _, _ = host(handler)

    with pytest.raises(PublishError, match="not ready after 3 s; publish again later"):
        code_host.ensure_fork(REPO)


def moved_handler(taken: set[str], current: dict[str, Any] | None = None) -> Handler:
    """GitHub for `student-a/webapi-hw`, now at `current`, where the publishing account
    already has repos named `taken`."""
    source = current or {"name": "webapi-hw", "owner": {"login": "student-a"}}

    def handler(method: str, path: str, body: Any) -> Reply:
        if path == "/repos/student-a/webapi-hw":
            return 200, source
        if method == "POST":
            if body["name"] in taken:
                return 403, {"message": "Name already exists on this account"}
            return 202, {"name": body["name"], "owner": {"login": ACCOUNT}}
        return 200, {}

    return handler


def test_ensure_fork_retries_with_the_repo_name_when_the_fork_name_is_taken() -> None:
    code_host, sender, _ = host(moved_handler({"check_student-a"}))

    fork = code_host.ensure_fork(OTHER_REPO)

    assert fork.name == "check_student-a-webapi-hw"
    posts = [sent_json(r)["name"] for r in sender.requests if r.get_method() == "POST"]
    assert posts == ["check_student-a", "check_student-a-webapi-hw"]


def test_ensure_fork_names_both_tried_names_when_both_are_taken() -> None:
    code_host, sender, _ = host(moved_handler({"check_student-a", "check_student-a-webapi-hw"}))

    with pytest.raises(PublishError) as raised:
        code_host.ensure_fork(OTHER_REPO)

    assert "check_student-a and check_student-a-webapi-hw" in str(raised.value)
    assert len([r for r in sender.requests if r.get_method() == "POST"]) == 2


def test_another_fork_error_is_not_retried() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if method == "GET":
            return 200, SOURCE
        return 403, {"message": "Resource not accessible by integration"}

    code_host, sender, _ = host(handler)

    with pytest.raises(PublishError, match="HTTP 403"):
        code_host.ensure_fork(REPO)

    assert len([r for r in sender.requests if r.get_method() == "POST"]) == 1


def test_a_moved_repo_is_forked_at_its_current_address_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    current = {"name": "webapi-hw2", "owner": {"login": "student-b"}}
    code_host, sender, _ = host(moved_handler(set(), current))

    with caplog.at_level(logging.WARNING):
        fork = code_host.ensure_fork(OTHER_REPO)

    assert fork.name == "check_student-b"
    assert ("POST", "/repos/student-b/webapi-hw2/forks") in sender.calls()
    assert ("POST", "/repos/student-a/webapi-hw/forks") not in sender.calls()
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "roster link of student-a-webapi-hw is out of date" in record.getMessage()
    assert "student-b/webapi-hw2" in record.getMessage()
    assert record.__dict__["repo_id"] == "student-a-webapi-hw"


def test_ensure_fork_needs_the_publishing_account() -> None:
    code_host, sender, _ = host(lambda m, p, b: (202, {}), account=None)

    with pytest.raises(PublishError, match=r"set github\.account in config\.yaml"):
        code_host.ensure_fork(REPO)

    assert sender.requests == []


def test_a_new_pull_request_is_opened_from_the_fork_with_maintainer_edits() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if method == "GET":
            return 200, []
        return 201, {"number": 4, "html_url": "https://github.com/dana/api/pull/4"}

    code_host, sender, _ = host(handler)

    pull = code_host.open_or_update_pull_request(
        REPO, FORK, "hw-review/hw01-2026-11-01", "main", "HW1 review", "Body"
    )

    assert (pull.number, pull.created) == (4, True)
    assert sender.calls()[0] == (
        "GET",
        "/repos/dana/api/pulls?state=open&head=review-bot%3Ahw-review%2Fhw01-2026-11-01",
    )
    assert sender.calls()[1] == ("POST", "/repos/dana/api/pulls")
    assert sent_json(sender.requests[1]) == {
        "title": "HW1 review",
        "body": "Body",
        "head": "review-bot:hw-review/hw01-2026-11-01",
        "base": "main",
        "maintainer_can_modify": True,
    }


def test_publishing_again_updates_the_open_pull_request() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        if method == "GET":
            return 200, [{"number": 4}]
        return 200, {"number": 4, "html_url": "https://github.com/dana/api/pull/4"}

    code_host, sender, _ = host(handler)

    pull = code_host.open_or_update_pull_request(REPO, FORK, "b", "main", "New title", "New body")

    assert (pull.number, pull.created) == (4, False)
    assert sender.calls()[1] == ("PATCH", "/repos/dana/api/pulls/4")
    assert sent_json(sender.requests[1]) == {"title": "New title", "body": "New body"}


def test_an_api_error_carries_githubs_message() -> None:
    reply = {
        "message": "Validation Failed",
        "errors": [{"message": "No commits between main and b"}],
    }
    code_host, _, _ = host(lambda m, p, b: (200, []) if m == "GET" else (422, reply))

    with pytest.raises(PublishError) as raised:
        code_host.open_or_update_pull_request(REPO, FORK, "b", "main", "T", "B")

    assert str(raised.value) == (
        "GitHub POST /repos/dana/api/pulls failed (HTTP 422): Validation Failed; "
        "No commits between main and b"
    )


def test_an_unreachable_github_is_a_publish_error() -> None:
    def handler(method: str, path: str, body: Any) -> Reply:
        raise urllib.error.URLError("connection refused")

    code_host, _, _ = host(handler)

    with pytest.raises(PublishError, match=r"Cannot reach GitHub at https://api\.github\.test"):
        code_host.default_branch_head(REPO)


def test_a_reply_without_the_expected_field_is_a_publish_error() -> None:
    code_host, _, _ = host(lambda m, p, b: (200, {"name": "api"}))

    with pytest.raises(PublishError, match="has no default_branch"):
        code_host.default_branch_head(REPO)


def test_a_reply_that_is_not_json_is_a_publish_error() -> None:
    code_host, _, _ = host(lambda m, p, b: (200, b"OK"))

    with pytest.raises(PublishError, match="not JSON"):
        code_host.default_branch_head(REPO)


@pytest.mark.parametrize("token", [None, " "])
def test_nothing_is_sent_without_a_token(token: str | None) -> None:
    code_host, sender, _ = host(lambda m, p, b: (200, {}), token=token)

    with pytest.raises(PublishError, match="HW_CHECKER_GITHUB_TOKEN"):
        code_host.default_branch_head(REPO)

    assert sender.requests == []


def test_the_token_never_appears_in_an_error_or_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    echoed = {"message": f"Bad credentials for {FAKE_TOKEN}"}
    code_host, _, _ = host(lambda m, p, b: (401, echoed))

    with caplog.at_level(logging.DEBUG), pytest.raises(PublishError) as raised:
        code_host.default_branch_head(REPO)

    assert FAKE_TOKEN not in str(raised.value)
    assert "Bad credentials for ***" in str(raised.value)
    for record in caplog.records:
        assert FAKE_TOKEN not in record.getMessage()
        assert FAKE_TOKEN not in repr(record.__dict__)
