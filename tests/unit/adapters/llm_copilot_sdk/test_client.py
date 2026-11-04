"""The Copilot SDK LLM client over a fake runtime; never a real call."""

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest
from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.session_events import SessionErrorData
from pydantic import BaseModel, SecretStr

from hw_checker.adapters.llm_copilot_sdk import CopilotClientConfig, CopilotSdkLLMClient
from hw_checker.domain.errors import LLMError, LLMOutputError
from hw_checker.ports.llm import TokenUsage
from tests.unit.adapters.llm_copilot_sdk.fake_sdk import FakeCopilot, Step

TOKEN = "github_pat_not_a_real_token_42"  # pragma: allowlist secret
PROMPT = "Synthetic prompt: say pong."
PONG = '{"answer": "pong"}'
MODELS = {"reviewer": "claude-haiku-5.5", "judge": "claude-sonnet-5.5"}
LOGGER = "hw_checker.adapters.llm_copilot_sdk.client"


class Pong(BaseModel):
    answer: str


@dataclass
class Harness:
    copilot: FakeCopilot
    client: CopilotSdkLLMClient
    sleeps: list[float]


class MakeClient(Protocol):
    def __call__(
        self,
        *steps: Step,
        token: str | None = None,
        timeout_seconds: float = 5,
        start_timeout_seconds: float = 5,
        max_retries: int = 2,
        role_timeouts: dict[str, float] | None = None,
        **copilot: Any,
    ) -> Harness: ...


@pytest.fixture
def make() -> Iterator[MakeClient]:
    """Builds a client over a `FakeCopilot` scripted with `steps` (default: one pong) and
    the given fake options; every client is closed after the test."""
    built: list[CopilotSdkLLMClient] = []

    def factory(
        *steps: Step,
        token: str | None = None,
        timeout_seconds: float = 5,
        start_timeout_seconds: float = 5,
        max_retries: int = 2,
        role_timeouts: dict[str, float] | None = None,
        **copilot_options: Any,
    ) -> Harness:
        copilot = FakeCopilot(steps=list(steps) or [PONG], **copilot_options)
        config = CopilotClientConfig(
            models=MODELS,
            timeout_seconds=timeout_seconds,
            start_timeout_seconds=start_timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=1,
            max_prompt_chars=1000,
            role_timeouts=role_timeouts or {},
        )
        sleeps: list[float] = []
        client = CopilotSdkLLMClient(
            config,
            SecretStr(token) if token is not None else None,
            runtime_factory=copilot.factory,
            sleep=sleeps.append,
        )
        built.append(client)
        return Harness(copilot, client, sleeps)

    yield factory
    for client in built:
        client.close()


def session_error(error_type: str, status: int | None = None, message: str = "no") -> Step:
    return SessionErrorData(error_type=error_type, message=message, status_code=status)


def test_each_role_calls_its_own_model_in_a_new_session(make: MakeClient) -> None:
    harness = make()

    harness.client.complete("reviewer", PROMPT, Pong)
    harness.client.complete("judge", PROMPT, Pong)

    models = [options["model"] for options in harness.copilot.session_options()]
    assert models == ["claude-haiku-5.5", "claude-sonnet-5.5"]
    assert len(harness.copilot.runtimes) == 1


def test_a_valid_reply_is_parsed_into_the_output_type(make: MakeClient) -> None:
    assert make().client.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")


def test_the_prompt_is_sent_as_text_with_the_schema_at_the_end(make: MakeClient) -> None:
    harness = make()

    harness.client.complete("reviewer", PROMPT, Pong)

    [session] = harness.copilot.sessions()
    [sent] = session.sent
    assert sent.prompt.startswith(PROMPT + "\n\n# Reply format\n")
    assert '"required":["answer"]' in sent.prompt
    assert '"additionalProperties":false' in sent.prompt
    assert sent.timeout == 5


def test_a_role_with_its_own_timeout_uses_it(make: MakeClient) -> None:
    harness = make(PONG, role_timeouts={"judge": 120})

    harness.client.complete("judge", PROMPT, Pong)
    harness.client.complete("reviewer", PROMPT, Pong)

    timeouts = [sent.timeout for session in harness.copilot.sessions() for sent in session.sent]
    assert timeouts == [120, 5]


def test_a_reply_after_a_timeout_comes_from_a_new_session(make: MakeClient) -> None:
    harness = make(TimeoutError(), '{"answer": "late"}')

    assert harness.client.complete("reviewer", PROMPT, Pong) == Pong(answer="late")

    first, second = harness.copilot.sessions()
    assert first.aborted and first.disconnected
    assert len(first.sent) == 1
    assert not second.aborted


def test_sessions_get_no_tools_and_an_empty_temporary_working_directory(
    make: MakeClient,
) -> None:
    harness = make()

    harness.client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    [options] = harness.copilot.session_options()
    assert (options["available_tools"], options["mcp_servers"]) == ([], {})
    work_dir = Path(options["working_directory"])
    assert runtime.options["working_directory"] == str(work_dir)
    assert work_dir.is_dir()
    assert list(work_dir.iterdir()) == []
    assert Path.cwd() not in [work_dir, *work_dir.parents]
    harness.client.close()
    assert not work_dir.exists()


def test_every_session_is_ended_and_deleted_after_its_call(make: MakeClient) -> None:
    harness = make()

    harness.client.complete("reviewer", PROMPT, Pong)
    harness.client.complete("judge", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert all(session.disconnected for session in harness.copilot.sessions())
    assert runtime.deleted == ["session-1", "session-2"]


def test_without_a_token_the_logged_in_user_is_used(make: MakeClient) -> None:
    harness = make()

    harness.client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert runtime.options["use_logged_in_user"] is True
    assert "github_token" not in runtime.options


def test_a_token_reaches_only_the_runtime_options(make: MakeClient) -> None:
    harness = make(token=TOKEN)

    harness.client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert runtime.options["github_token"] == TOKEN
    assert runtime.options["mode"] == "empty"
    assert all(TOKEN not in str(options) for options in harness.copilot.session_options())


def test_a_role_without_a_model_is_refused_without_starting_the_runtime(
    make: MakeClient,
) -> None:
    harness = make()

    with pytest.raises(LLMError, match="No model is configured for the LLM role 'fixer'"):
        harness.client.complete("fixer", PROMPT, Pong)

    assert harness.copilot.runtimes == []


def test_a_prompt_that_is_too_long_is_refused_before_sending(make: MakeClient) -> None:
    harness = make()

    with pytest.raises(LLMError, match="1001 characters, more than the limit of 1000"):
        harness.client.complete("reviewer", "x" * 1001, Pong)

    assert harness.copilot.runtimes == []


def test_an_unreadable_reply_is_retried_once_with_the_problems_as_feedback(
    make: MakeClient,
) -> None:
    harness = make("I think the answer is pong.", PONG)

    assert harness.client.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")

    [session] = harness.copilot.sessions()
    first, retry = session.sent
    assert first.prompt.startswith(PROMPT)
    assert "no JSON object" in retry.prompt
    assert "I think the answer" not in retry.prompt


def test_a_reply_still_unreadable_after_the_feedback_is_an_llm_error(make: MakeClient) -> None:
    harness = make('{"wrong": 1}')

    with pytest.raises(LLMError, match="could not be read as Pong"):
        harness.client.complete("reviewer", PROMPT, Pong)

    [session] = harness.copilot.sessions()
    assert len(session.sent) == 2
    assert "answer: Field required" in session.sent[1].prompt


def test_a_run_without_a_reply_is_an_llm_error(make: MakeClient) -> None:
    with pytest.raises(LLMError, match="without a reply"):
        make(None).client.complete("reviewer", PROMPT, Pong)


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (session_error("authentication", 401), "Copilot refused the login"),
        (session_error("authorization", 403, "no seat"), "no Copilot seat"),
        (session_error("policy", None, "model disabled"), "not enabled for it"),
        (session_error("quota", 402, "monthly limit"), "premium-request allowance is used up"),
        (session_error("context_limit"), "The prompt is too long for claude-haiku-5.5"),
        (session_error("query", 400, "bad request"), "Copilot rejected the request: bad request"),
        (JsonRpcError(-32603, "Model not available"), "rejected the request: Model not avail"),
        (ProcessExitedError("gone"), "The Copilot CLI stopped during the call"),
    ],
)
def test_failures_that_cannot_pass_are_not_retried(
    make: MakeClient, step: Step, message: str
) -> None:
    harness = make(step, PONG)

    with pytest.raises(LLMError, match=message):
        harness.client.complete("reviewer", PROMPT, Pong)

    assert len(harness.copilot.sessions()) == 1
    assert harness.sleeps == []


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (session_error("rate_limit", 429), "rate limit was reached"),
        (session_error("query", 503), "The Copilot service failed \\(HTTP 503\\)"),
        (TimeoutError(), "did not answer within 5 s"),
    ],
)
def test_transient_failures_are_retried_with_backoff(
    make: MakeClient, step: Step, message: str
) -> None:
    harness = make(step)

    with pytest.raises(LLMError, match=f"{message} \\(after 3 attempts\\)"):
        harness.client.complete("reviewer", PROMPT, Pong)

    assert len(harness.copilot.sessions()) == 3
    assert harness.sleeps == [1, 2]


def test_a_transient_failure_followed_by_a_reply_succeeds(make: MakeClient) -> None:
    harness = make(session_error("rate_limit", 429), PONG)

    assert harness.client.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")
    assert harness.sleeps == [1]


def test_a_timed_out_run_is_aborted(make: MakeClient) -> None:
    harness = make(TimeoutError(), max_retries=0)

    with pytest.raises(LLMError, match="did not answer within 5 s"):
        harness.client.complete("reviewer", PROMPT, Pong)

    [session] = harness.copilot.sessions()
    assert session.aborted
    assert session.disconnected


def test_a_session_that_does_not_open_in_time_is_a_timeout(make: MakeClient) -> None:
    harness = make(create_delay=1, timeout_seconds=0.01, max_retries=0)

    with pytest.raises(LLMError, match=re.escape("did not answer within 0.01 s")):
        harness.client.complete("reviewer", PROMPT, Pong)


def test_after_the_cli_stops_the_next_call_starts_a_new_runtime(make: MakeClient) -> None:
    harness = make(ProcessExitedError("gone"), PONG)

    with pytest.raises(LLMError, match="stopped during the call"):
        harness.client.complete("reviewer", PROMPT, Pong)
    assert harness.client.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")

    first, second = harness.copilot.runtimes
    assert (first.stops, second.stops) == (1, 0)


def test_a_runtime_without_a_login_says_how_to_log_in(make: MakeClient) -> None:
    harness = make(authenticated=False)

    with pytest.raises(LLMError, match="Copilot is not logged in; run the Copilot login"):
        harness.client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert runtime.stops == 1
    assert runtime.sessions == []
    assert harness.sleeps == []


@pytest.mark.parametrize(
    "options",
    [
        {"factory_error": RuntimeError("Failed to download the runtime")},
        {"start_error": FileNotFoundError("copilot-runtime.exe")},
        {"start_error": JsonRpcError(-32000, "protocol mismatch")},
    ],
)
def test_a_cli_that_cannot_start_is_an_llm_error(make: MakeClient, options: dict[str, Any]) -> None:
    harness = make(**options)

    with pytest.raises(LLMError, match="The Copilot CLI could not start"):
        harness.client.complete("reviewer", PROMPT, Pong)

    assert harness.sleeps == []


def test_a_cli_that_does_not_start_in_time_is_an_llm_error(make: MakeClient) -> None:
    harness = make(start_delay=1, start_timeout_seconds=0.01)

    with pytest.raises(LLMError, match=re.escape("could not start (no answer within 0.01 s)")):
        harness.client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert runtime.stops == 1


def test_the_runtime_starts_once_and_stops_once_on_close(make: MakeClient) -> None:
    harness = make()

    for _ in range(3):
        harness.client.complete("reviewer", PROMPT, Pong)
    harness.client.close()
    harness.client.close()

    [runtime] = harness.copilot.runtimes
    assert runtime.started
    assert runtime.stops == 1


def test_a_closed_client_refuses_calls(make: MakeClient) -> None:
    harness = make()
    harness.client.close()

    with pytest.raises(LLMError, match="client is closed"):
        harness.client.complete("reviewer", PROMPT, Pong)


def test_a_with_block_closes_the_client(make: MakeClient) -> None:
    harness = make()

    with harness.client as client:
        client.complete("reviewer", PROMPT, Pong)

    [runtime] = harness.copilot.runtimes
    assert runtime.stops == 1


def test_closing_a_client_that_never_ran_does_nothing(make: MakeClient) -> None:
    harness = make()

    harness.client.close()

    assert harness.copilot.runtimes == []


def test_complete_with_usage_returns_the_reply_and_the_sessions_tokens(
    make: MakeClient,
) -> None:
    reply, usage = make().client.complete_with_usage("reviewer", PROMPT, Pong)

    assert reply == Pong(answer="pong")
    assert usage == TokenUsage(input_tokens=100, output_tokens=7)


def test_an_unreadable_reply_is_an_output_error_the_graph_can_retry(make: MakeClient) -> None:
    harness = make('{"wrong": 1}')

    with pytest.raises(LLMOutputError, match="could not be read as Pong"):
        harness.client.complete_with_usage("reviewer", PROMPT, Pong)


def test_each_call_logs_usage_but_never_the_prompt_reply_or_token(
    make: MakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    reply = '{"answer": "pong-reply-text"}'
    harness = make(reply, token=TOKEN)

    with caplog.at_level(logging.DEBUG):
        harness.client.complete("reviewer", PROMPT, Pong)

    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert record.__dict__["llm_provider"] == "copilot"
    assert (record.__dict__["llm_role"], record.__dict__["llm_model"]) == (
        "reviewer",
        "claude-haiku-5.5",
    )
    assert (record.__dict__["outcome"], record.__dict__["attempts"]) == ("ok", 1)
    assert (record.__dict__["input_tokens"], record.__dict__["output_tokens"]) == (100, 7)
    assert record.__dict__["premium_requests"] == 1.0
    for logged in caplog.records:
        text = f"{logged.getMessage()} {logged.__dict__}"
        assert PROMPT not in text
        assert "pong-reply-text" not in text
        assert TOKEN not in text


def test_the_token_never_appears_in_an_error_or_a_log_record(
    make: MakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    harness = make(session_error("query", 400, f"bad token {TOKEN}"), token=TOKEN)

    with caplog.at_level(logging.DEBUG), pytest.raises(LLMError) as raised:
        harness.client.complete("reviewer", PROMPT, Pong)

    assert TOKEN not in str(raised.value)
    assert "[redacted]" in str(raised.value)
    assert all(TOKEN not in f"{r.getMessage()} {r.__dict__}" for r in caplog.records)


def test_a_failed_call_logs_its_outcome_and_attempts(
    make: MakeClient, caplog: pytest.LogCaptureFixture
) -> None:
    harness = make(session_error("quota", 402))

    with caplog.at_level(logging.INFO), pytest.raises(LLMError):
        harness.client.complete("reviewer", PROMPT, Pong)

    [record] = [r for r in caplog.records if r.name == LOGGER]
    assert (record.__dict__["outcome"], record.__dict__["attempts"]) == ("allowance", 1)
