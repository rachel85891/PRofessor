"""OpenAICompatibleLLMClient: `LLMClient` over any OpenAI-compatible endpoint (ADR 0020).

Each role's model is called through `langchain-openai`'s `ChatOpenAI` with structured
output (`json_schema`), so the reply is validated into the caller's Pydantic model. A role
in `json_prompt` mode gets JSON mode instead, with the schema written in the prompt, and its
reply is validated afterwards: the author's schema is too large for strict output on Claude.

Retries happen here, not in the OpenAI SDK (whose own retries are off), so only rate
limits, timeouts and server errors are retried and every attempt is counted. The prompt
and the reply are never logged, and the API key never appears in a message or a log.
"""

import json
import logging
import re
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

import httpx2
import openai
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from hw_checker.domain.errors import LLMError, LLMOutputError
from hw_checker.ports.llm import TokenUsage

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)
Clock = Callable[[], float]
Sleep = Callable[[float], None]

MAX_BACKOFF_SECONDS = 30.0
MAX_PROVIDER_MESSAGE_CHARS = 200
REDACTED = "[redacted]"
SERVER_ERROR_STATUS = 500
PAYMENT_REQUIRED_STATUS = 402
ROUTING_FAILURE = re.compile(r"no endpoints found|parameter", re.IGNORECASE)
"""A 404 whose provider message matches is a routing failure, not an unknown model."""
SDK_STATUS_PREFIX = re.compile(r"^Error code: \d+ - ")
"""The SDK's own prefix on the provider's message; our messages give the status already."""
JSON_PROMPT = "json_prompt"
"""The output mode that sends the schema in the prompt and validates the reply afterwards."""
JSON_PROMPT_INSTRUCTION = (
    "Reply with one JSON object that matches this JSON Schema, and nothing else."
)
JSON_OBJECT = {"type": "json_object"}
JSON_FENCE = re.compile(r"\A\s*```(?:json)?[ \t]*\n(.*?)\n?\s*```\s*\Z", re.DOTALL | re.IGNORECASE)
"""A Markdown code fence around the whole reply, which a model may add in JSON mode."""


@dataclass(frozen=True)
class LlmClientConfig:
    """The endpoint, the model of each role, and the call limits."""

    base_url: str
    models: Mapping[str, str]
    """The model id for each role."""
    temperature: float | None
    """None leaves it out of the request."""
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_backoff_seconds: float
    max_prompt_chars: int
    extra_body: Mapping[str, Any]
    """Extra JSON fields for every request (OpenRouter's provider routing)."""
    role_timeouts: Mapping[str, float] = field(default_factory=dict)
    """A role's own longest wait for one reply, instead of `timeout_seconds`."""
    role_max_prompt_chars: Mapping[str, int] = field(default_factory=dict)
    """A role's own prompt limit, instead of `max_prompt_chars`."""
    role_max_output_tokens: Mapping[str, int] = field(default_factory=dict)
    """A role's own reply limit, instead of `max_output_tokens`."""
    role_output_modes: Mapping[str, str] = field(default_factory=dict)
    """A role's own output mode, `json_schema` (strict structured output, the default) or
    `json_prompt` (JSON mode, with the schema in the prompt)."""

    def timeout(self, role: str) -> float:
        """The longest wait for one reply of `role`."""
        return self.role_timeouts.get(role, self.timeout_seconds)

    def prompt_limit(self, role: str) -> int:
        """The longest prompt of `role`, in characters."""
        return self.role_max_prompt_chars.get(role, self.max_prompt_chars)

    def output_tokens(self, role: str) -> int:
        """The most tokens one reply of `role` may have."""
        return self.role_max_output_tokens.get(role, self.max_output_tokens)

    def output_mode(self, role: str) -> str:
        """How the reply of `role` is asked for: `json_schema` or `json_prompt`."""
        return self.role_output_modes.get(role, "json_schema")


@dataclass(frozen=True)
class _Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None


_OUTPUT_FAILURES = frozenset({"unparseable", "unexpected_reply"})
"""Outcomes where the model answered but its reply could not be read."""


class _AttemptError(LLMError):
    """One failed attempt: the outcome for the log, and whether a retry may help."""

    def __init__(self, outcome: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.retryable = retryable


class OpenAICompatibleLLMClient:
    """`LLMClient` that calls the model configured for each role at one endpoint."""

    def __init__(
        self,
        config: LlmClientConfig,
        api_key: SecretStr,
        http_client: httpx2.Client | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._config = config
        self._api_key = api_key
        self._clock = clock
        self._sleep = sleep
        self._chats = {
            role: ChatOpenAI(
                model_name=model,
                openai_api_base=config.base_url,
                openai_api_key=api_key,
                temperature=config.temperature,
                max_tokens=config.output_tokens(role),
                request_timeout=config.timeout(role),
                max_retries=0,  # retried here, so only the right errors are
                extra_body=dict(config.extra_body) or None,
                http_client=http_client,
            )
            for role, model in config.models.items()
        }

    def complete(self, role: str, prompt: str, output_type: type[OutputT]) -> OutputT:
        """Send `prompt` to the model for `role` and parse the reply into `output_type`.

        Raises:
            LLMError: the role has no model, the prompt is too long, the call failed after
                any retries, or the reply could not be parsed into `output_type`.
        """
        return self.complete_with_usage(role, prompt, output_type)[0]

    def complete_with_usage(
        self, role: str, prompt: str, output_type: type[OutputT]
    ) -> tuple[OutputT, TokenUsage]:
        """Like `complete`, and also return the tokens the endpoint reported.

        Raises:
            LLMOutputError: the model answered, but the reply could not be parsed.
            LLMError: the role has no model, the prompt is too long, or the call failed
                after any retries.
        """
        model = self._config.models.get(role)
        if model is None:
            raise LLMError(f"No model is configured for the LLM role '{role}'.")
        json_prompt = self._config.output_mode(role) == JSON_PROMPT
        if json_prompt:
            prompt = _with_schema(prompt, output_type)
        limit = self._config.prompt_limit(role)
        if len(prompt) > limit:
            raise LLMError(
                f"The prompt for {role} has {len(prompt)} characters, more than the limit of "
                f"{limit}; it was not sent."
            )
        started = self._clock()
        attempts = 0
        outcome = "error"
        usage = _Usage()
        try:
            while True:
                attempts += 1
                try:
                    result, usage = self._attempt(role, model, prompt, output_type, json_prompt)
                except _AttemptError as failure:
                    outcome = failure.outcome
                    if not failure.retryable or attempts > self._config.max_retries:
                        error = LLMOutputError if failure.outcome in _OUTPUT_FAILURES else LLMError
                        raise error(_with_attempts(str(failure), attempts)) from None
                    self._sleep(self._backoff(attempts))
                    continue
                outcome = "ok"
                return result, TokenUsage(
                    input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0
                )
        finally:
            logger.info(
                "LLM call for %s (%s): %s in %.1f s, %s input and %s output tokens, %d attempts",
                role,
                model,
                outcome,
                self._clock() - started,
                usage.input_tokens,
                usage.output_tokens,
                attempts,
                extra={
                    "llm_role": role,
                    "llm_model": model,
                    "outcome": outcome,
                    "duration_seconds": round(self._clock() - started, 3),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "attempts": attempts,
                },
            )

    def _attempt(
        self, role: str, model: str, prompt: str, output_type: type[OutputT], json_prompt: bool
    ) -> tuple[OutputT, _Usage]:
        try:
            if json_prompt:
                message = self._chats[role].invoke(prompt, response_format=JSON_OBJECT)
                return _parse_json_reply(message.content, output_type), _usage(message)
            runnable = self._chats[role].with_structured_output(
                output_type, method="json_schema", include_raw=True
            )
            reply = cast(dict[str, Any], runnable.invoke(prompt))
        except openai.OpenAIError as error:
            raise self._failure(model, error, self._config.timeout(role)) from None
        except ValueError:
            # The SDK validates the reply against the schema; JSON and Pydantic errors are
            # ValueErrors. Their text may quote the reply, so it is not kept.
            raise self._unparseable(model, output_type) from None
        except (AttributeError, TypeError):
            # The SDK got a body that is not a chat completion at all, such as the plain
            # "OK" that a blocking proxy sends instead of GitHub Models' reply.
            raise _AttemptError(
                "unexpected_reply",
                f"The LLM endpoint {self._config.base_url} did not answer like an "
                "OpenAI-compatible API; check llm.base_url, or a proxy or firewall.",
            ) from None
        usage = _usage(reply.get("raw"))
        parsed = reply.get("parsed")
        if reply.get("parsing_error") is not None or not isinstance(parsed, output_type):
            raise self._unparseable(model, output_type)
        return parsed, usage

    def _failure(self, model: str, error: openai.OpenAIError, timeout: float) -> _AttemptError:
        if isinstance(error, openai.AuthenticationError | openai.PermissionDeniedError):
            return _AttemptError(
                "auth",
                f"The LLM endpoint refused the API key (HTTP {error.status_code}); check "
                "HW_CHECKER_LLM__API_KEY in .env.",
            )
        if isinstance(error, openai.RateLimitError):
            return _AttemptError(
                "rate_limit", "The LLM endpoint's rate limit was reached.", retryable=True
            )
        if isinstance(error, openai.APITimeoutError):
            return _AttemptError(
                "timeout",
                f"The model {model} did not answer within {timeout:g} s.",
                retryable=True,
            )
        if isinstance(error, openai.APIConnectionError):
            if _caused_by_tls(error):
                return _AttemptError(
                    "network",
                    "The LLM endpoint's TLS certificate is not trusted; check the network's "
                    "certificate (a TLS-inspecting proxy needs its root certificate in the "
                    "system store).",
                )
            return _AttemptError(
                "network",
                f"Cannot reach the LLM endpoint {self._config.base_url}; check the network.",
            )
        if isinstance(error, openai.APIStatusError):
            if error.status_code >= SERVER_ERROR_STATUS:
                return _AttemptError(
                    "server_error",
                    f"The LLM endpoint failed (HTTP {error.status_code}).",
                    retryable=True,
                )
            if error.status_code == PAYMENT_REQUIRED_STATUS:
                return _AttemptError(
                    "no_credit",
                    "The LLM account is out of credit, or the request asks for too many "
                    f"tokens (HTTP 402): {self._provider_message(error)}",
                )
            if isinstance(error, openai.NotFoundError) and ROUTING_FAILURE.search(
                str(error.message)
            ):
                return _AttemptError(
                    "unroutable",
                    "No provider accepts the requested parameters for the model "
                    f"{model} (HTTP 404): {self._provider_message(error)}",
                )
            if isinstance(error, openai.NotFoundError):
                return _AttemptError(
                    "not_found",
                    f"The LLM endpoint does not know the model {model} (HTTP 404); check "
                    "llm.roles in config.yaml.",
                )
            return _AttemptError(
                "rejected",
                f"The LLM endpoint rejected the request (HTTP {error.status_code}): "
                f"{self._provider_message(error)}",
            )
        if isinstance(
            error, openai.LengthFinishReasonError | openai.ContentFilterFinishReasonError
        ):
            return _AttemptError("unparseable", f"The reply of {model} was cut off or filtered.")
        return _AttemptError("error", f"The LLM call failed: {type(error).__name__}.")

    @staticmethod
    def _unparseable(model: str, output_type: type[BaseModel]) -> _AttemptError:
        return _AttemptError(
            "unparseable", f"The reply of {model} could not be read as {output_type.__name__}."
        )

    def _provider_message(self, error: openai.APIStatusError) -> str:
        """The provider's own message, short and with the key removed, just in case.

        OpenRouter's "Provider returned error" hides the cause in `error.metadata.raw`, so
        the message there is added when there is one.
        """
        message = SDK_STATUS_PREFIX.sub("", str(error.message))
        raw = _raw_provider_message(error.body)
        if raw:
            message = f"{message.removesuffix('.')}: {raw}"
        message = " ".join(message.split())
        key = self._api_key.get_secret_value()
        if key:
            message = message.replace(key, REDACTED)
        return message[:MAX_PROVIDER_MESSAGE_CHARS]

    def _backoff(self, attempt: int) -> float:
        return float(
            min(self._config.retry_backoff_seconds * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
        )


def _with_schema(prompt: str, output_type: type[BaseModel]) -> str:
    schema = json.dumps(output_type.model_json_schema(), separators=(",", ":"))
    return f"{prompt}\n\n{JSON_PROMPT_INSTRUCTION}\n{schema}"


def _parse_json_reply[ReplyT: BaseModel](content: object, output_type: type[ReplyT]) -> ReplyT:
    """The reply validated into `output_type`, or a ValueError (never quoting the reply)
    when it is not one JSON object of that shape."""
    if not isinstance(content, str):
        raise ValueError("the reply is not text")
    fenced = JSON_FENCE.match(content)
    return output_type.model_validate_json(fenced.group(1) if fenced else content)


def _raw_provider_message(body: object) -> str | None:
    """The upstream provider's message in OpenRouter's `metadata.raw`, if there is one."""
    metadata = body.get("metadata") if isinstance(body, dict) else None
    raw = metadata.get("raw") if isinstance(metadata, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed: object = json.loads(raw)
    except ValueError:
        return raw
    while isinstance(parsed, dict):
        parsed = parsed.get("error", parsed.get("message"))
    return parsed if isinstance(parsed, str) and parsed.strip() else raw


def _usage(raw: object) -> _Usage:
    metadata = getattr(raw, "usage_metadata", None)
    if not metadata:
        return _Usage()
    return _Usage(metadata.get("input_tokens"), metadata.get("output_tokens"))


def _caused_by_tls(error: BaseException) -> bool:
    cause: BaseException | None = error
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _with_attempts(message: str, attempts: int) -> str:
    if attempts == 1:
        return message
    return f"{message.removesuffix('.')} (after {attempts} attempts)."
