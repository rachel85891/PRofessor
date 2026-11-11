"""The OpenAI-compatible LLM client over a fake HTTP transport; never a real call."""

import json
import logging
import ssl
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from hw_checker.adapters.llm_openai_compatible import LlmClientConfig, OpenAICompatibleLLMClient
from hw_checker.domain.errors import LLMError, LLMOutputError
from hw_checker.ports.llm import TokenUsage

FAKE_KEY = "sk-or-not-a-real-key-123"  # pragma: allowlist secret
PROMPT = "Synthetic prompt: say pong."
MODELS = {"reviewer": "cheap/model-a", "judge": "strong/model-b"}
Handler = Callable[[httpx2.Request], httpx2.Response]


class Pong(BaseModel):
    answer: str


def completion(content: str, prompt_tokens: int = 12, completion_tokens: int = 5) -> Any:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "cheap/model-a",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def pong(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=completion('{"answer": "pong"}'))


def status(code: int, message: str = "error") -> Handler:
    return lambda request: httpx2.Response(code, json={"error": {"message": message, "code": code}})


def timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("timed out", request=request)


class Transport:
    """Answers with `handlers` in turn (the last one repeats) and keeps every request."""

    def __init__(self, *handlers: Handler) -> None:
        self._handlers = list(handlers)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        handler = self._handlers.pop(0) if len(self._handlers) > 1 else self._handlers[0]
        return handler(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def client(
    transport: Transport,
    *,
    max_retries: int = 2,
    max_prompt_chars: int = 1000,
    temperature: float | None = 0,
    role_timeouts: dict[str, float] | None = None,
    role_max_prompt_chars: dict[str, int] | None = None,
    role_max_output_tokens: dict[str, int] | None = None,
) -> tuple[OpenAICompatibleLLMClient, list[float]]:
    sleeps: list[float] = []
    config = LlmClientConfig(
        base_url="https://llm.test/api/v1",
        models=MODELS,
        temperature=temperature,
        max_output_tokens=256,
        timeout_seconds=5,
        max_retries=max_retries,
        retry_backoff_seconds=1,
        max_prompt_chars=max_prompt_chars,
        extra_body={"provider": {"data_collection": "deny", "require_parameters": True}},
        role_timeouts=role_timeouts or {},
        role_max_prompt_chars=role_max_prompt_chars or {},
        role_max_output_tokens=role_max_output_tokens or {},
    )
    llm = OpenAICompatibleLLMClient(
        config,
        SecretStr(FAKE_KEY),
        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)),
        sleep=sleeps.append,
    )
    return llm, sleeps


def test_each_role_calls_its_own_model_with_the_routing_options() -> None:
    transport = Transport(pong)
    llm, _ = client(transport)

    llm.complete("reviewer", PROMPT, Pong)
    llm.complete("judge", PROMPT, Pong)

    first, second = transport.bodies()
    assert (first["model"], second["model"]) == ("cheap/model-a", "strong/model-b")
    assert first["provider"] == {"data_collection": "deny", "require_parameters": True}
    assert first["temperature"] == 0
    assert first["max_completion_tokens"] == 256
    assert first["response_format"]["type"] == "json_schema"
    assert first["response_format"]["json_schema"]["name"] == "Pong"
    assert str(transport.requests[0].url) == "https://llm.test/api/v1/chat/completions"


def test_a_role_with_its_own_timeout_uses_it() -> None:
    transport = Transport(pong)
    llm, _ = client(transport, role_timeouts={"judge": 30})

    llm.complete("judge", PROMPT, Pong)
    llm.complete("reviewer", PROMPT, Pong)

    timeouts = [request.extensions["timeout"]["read"] for request in transport.requests]
    assert timeouts == [30, 5]


def test_a_timeout_names_the_roles_own_limit() -> None:
    llm, _ = client(Transport(timeout), max_retries=0, role_timeouts={"judge": 30})

    with pytest.raises(LLMError, match="did not answer within 30 s"):
        llm.complete("judge", PROMPT, Pong)


def test_no_temperature_is_sent_when_it_is_not_set() -> None:
    transport = Transport(pong)
    llm, _ = client(transport, temperature=None)

    llm.complete("reviewer", PROMPT, Pong)

    [body] = transport.bodies()
    assert "temperature" not in body
    assert body["max_completion_tokens"] == 256


def test_a_valid_reply_is_parsed_into_the_model() -> None:
    llm, _ = client(Transport(pong))

    assert llm.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")


def test_a_role_without_a_model_is_refused() -> None:
    transport = Transport(pong)
    llm, _ = client(transport)

    with pytest.raises(LLMError, match="No model is configured for the LLM role 'writer'"):
        llm.complete("writer", PROMPT, Pong)
    assert transport.requests == []


@pytest.mark.parametrize(
    "content", ["not json at all", '{"answer": 5}', '{"other": "pong"}'], ids=str
)
def test_an_unparseable_reply_is_an_llm_error(content: str) -> None:
    transport = Transport(lambda request: httpx2.Response(200, json=completion(content)))
    llm, sleeps = client(transport)

    with pytest.raises(LLMError, match="could not be read as Pong") as raised:
        llm.complete("reviewer", PROMPT, Pong)
    assert content not in str(raised.value)
    assert (len(transport.requests), sleeps) == (1, [])


def test_complete_with_usage_returns_the_reply_and_its_tokens() -> None:
    transport = Transport(
        lambda request: httpx2.Response(200, json=completion('{"answer": "pong"}', 40, 7))
    )
    llm, _ = client(transport)

    reply, usage = llm.complete_with_usage("reviewer", PROMPT, Pong)

    assert reply == Pong(answer="pong")
    assert usage == TokenUsage(input_tokens=40, output_tokens=7)


def test_an_unparseable_reply_is_an_output_error_the_graph_can_retry() -> None:
    transport = Transport(lambda request: httpx2.Response(200, json=completion("not json")))
    llm, _ = client(transport)

    with pytest.raises(LLMOutputError):
        llm.complete_with_usage("reviewer", PROMPT, Pong)


def test_a_failed_call_is_not_an_output_error() -> None:
    llm, _ = client(Transport(status(401, "No auth")), max_retries=0)

    with pytest.raises(LLMError) as raised:
        llm.complete_with_usage("reviewer", PROMPT, Pong)

    assert not isinstance(raised.value, LLMOutputError)


def test_a_reply_that_is_not_an_api_reply_is_an_llm_error() -> None:
    # A blocking proxy answers GitHub Models' requests with a plain "OK".
    plain = Transport(lambda request: httpx2.Response(200, text="OK"))
    llm, _ = client(plain)

    with pytest.raises(LLMError, match="did not answer like an OpenAI-compatible API"):
        llm.complete("reviewer", PROMPT, Pong)


@pytest.mark.parametrize(
    ("handler", "message", "requests"),
    [
        (status(401, "No auth credentials found"), "refused the API key \\(HTTP 401\\)", 1),
        (status(403, "Forbidden"), "refused the API key \\(HTTP 403\\)", 1),
        (status(404, "No such model"), "does not know the model cheap/model-a", 1),
        (
            status(404, "No endpoints found that can handle the requested parameters."),
            r"No provider accepts the requested parameters for the model cheap/model-a "
            r"\(HTTP 404\): No endpoints found that can handle the requested parameters\.$",
            1,
        ),
        (
            status(402, "This request requires more credits, or fewer max_tokens."),
            r"out of credit, or the request asks for too many tokens \(HTTP 402\): "
            r"This request requires more credits",
            1,
        ),
        (status(400, "No endpoints found"), "rejected the request \\(HTTP 400\\)", 1),
        (status(429, "Rate limited"), "rate limit was reached \\(after 3 attempts\\)", 3),
        (status(503, "Upstream down"), "failed \\(HTTP 503\\) \\(after 3 attempts\\)", 3),
        (timeout, "did not answer within 5 s \\(after 3 attempts\\)", 3),
    ],
    ids=[
        "auth",
        "forbidden",
        "unknown-model",
        "unroutable",
        "no-credit",
        "bad-request",
        "rate-limit",
        "5xx",
        "timeout",
    ],
)
def test_failures_become_llm_errors_and_only_transient_ones_are_retried(
    handler: Handler, message: str, requests: int
) -> None:
    transport = Transport(handler)
    llm, sleeps = client(transport)

    with pytest.raises(LLMError, match=message):
        llm.complete("reviewer", PROMPT, Pong)

    assert len(transport.requests) == requests
    assert sleeps == [1.0, 2.0][: requests - 1]


def test_a_transient_failure_followed_by_a_reply_succeeds() -> None:
    transport = Transport(status(429), timeout, pong)
    llm, sleeps = client(transport)

    assert llm.complete("reviewer", PROMPT, Pong) == Pong(answer="pong")
    assert (len(transport.requests), sleeps) == (3, [1.0, 2.0])


def test_no_retries_when_max_retries_is_zero() -> None:
    transport = Transport(status(429))
    llm, _ = client(transport, max_retries=0)

    with pytest.raises(LLMError, match=r"rate limit was reached\.$"):
        llm.complete("reviewer", PROMPT, Pong)
    assert len(transport.requests) == 1


def test_a_network_failure_is_an_llm_error() -> None:
    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    llm, _ = client(Transport(refused), max_retries=0)

    with pytest.raises(LLMError, match=r"Cannot reach the LLM endpoint https://llm\.test/api/v1"):
        llm.complete("reviewer", PROMPT, Pong)


def test_an_untrusted_certificate_names_the_tls_cause() -> None:
    def untrusted(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("certificate verify failed", request=request) from (
            ssl.SSLCertVerificationError("unable to get local issuer certificate")
        )

    llm, _ = client(Transport(untrusted), max_retries=0)

    with pytest.raises(LLMError) as raised:
        llm.complete("reviewer", PROMPT, Pong)

    assert str(raised.value) == (
        "The LLM endpoint's TLS certificate is not trusted; check the network's certificate "
        "(a TLS-inspecting proxy needs its root certificate in the system store)."
    )


def test_a_network_failure_is_not_retried() -> None:
    def refused(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    transport = Transport(refused)
    llm, _ = client(transport)

    with pytest.raises(LLMError, match=r"check the network\.$"):
        llm.complete("reviewer", PROMPT, Pong)

    assert len(transport.requests) == 1


def test_a_prompt_that_is_too_long_is_refused_before_sending() -> None:
    transport = Transport(pong)
    llm, _ = client(transport, max_prompt_chars=10)

    with pytest.raises(LLMError, match="more than the limit of 10; it was not sent"):
        llm.complete("reviewer", "x" * 11, Pong)
    assert transport.requests == []


def test_a_role_has_its_own_prompt_and_reply_limits_and_the_others_keep_theirs() -> None:
    transport = Transport(pong)
    llm, _ = client(
        transport,
        max_prompt_chars=10,
        role_max_prompt_chars={"judge": 20},
        role_max_output_tokens={"judge": 999},
    )

    llm.complete("judge", "x" * 20, Pong)
    with pytest.raises(LLMError, match="11 characters, more than the limit of 10"):
        llm.complete("reviewer", "x" * 11, Pong)

    [body] = transport.bodies()
    assert body["max_completion_tokens"] == 999
    llm.complete("reviewer", "x" * 10, Pong)
    assert transport.bodies()[1]["max_completion_tokens"] == 256


@pytest.mark.parametrize(
    "handler",
    [
        status(401, f"Invalid key {FAKE_KEY}"),
        status(400, f"Bad request for {FAKE_KEY}"),
        status(404, f"No endpoints found for {FAKE_KEY}"),
        status(402, f"More credits needed for {FAKE_KEY}"),
        pong,
    ],
    ids=["auth", "echoed-key", "unroutable", "no-credit", "success"],
)
def test_the_key_never_appears_in_an_error_or_a_log_record(
    handler: Handler, caplog: pytest.LogCaptureFixture
) -> None:
    llm, _ = client(Transport(handler), max_retries=0)

    with caplog.at_level(logging.DEBUG):
        try:
            llm.complete("reviewer", PROMPT, Pong)
        except LLMError as error:
            assert FAKE_KEY not in str(error)
            assert FAKE_KEY not in repr(error.__cause__)

    for record in caplog.records:
        assert FAKE_KEY not in record.getMessage()
        assert FAKE_KEY not in repr(record.__dict__)


def test_each_call_logs_tokens_and_duration_but_never_the_prompt_or_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = Transport(
        lambda request: httpx2.Response(200, json=completion('{"answer": "pong"}', 40, 7))
    )
    llm, _ = client(transport)

    with caplog.at_level(logging.DEBUG):
        llm.complete("reviewer", PROMPT, Pong)

    [record] = [r for r in caplog.records if r.name.endswith("llm_openai_compatible.client")]
    assert record.levelno == logging.INFO
    fields = record.__dict__
    assert (fields["llm_role"], fields["llm_model"], fields["outcome"]) == (
        "reviewer",
        "cheap/model-a",
        "ok",
    )
    assert (fields["input_tokens"], fields["output_tokens"], fields["attempts"]) == (40, 7, 1)
    assert fields["duration_seconds"] >= 0
    for logged in caplog.records:
        assert PROMPT not in logged.getMessage() and PROMPT not in repr(logged.__dict__)
        assert '"pong"' not in logged.getMessage()


def test_a_failed_call_logs_its_outcome(caplog: pytest.LogCaptureFixture) -> None:
    llm, _ = client(Transport(status(401)))

    with caplog.at_level(logging.INFO), pytest.raises(LLMError):
        llm.complete("judge", PROMPT, Pong)

    [record] = [r for r in caplog.records if r.name.endswith("llm_openai_compatible.client")]
    assert (record.__dict__["outcome"], record.__dict__["attempts"]) == ("auth", 1)
    assert record.__dict__["input_tokens"] is None
