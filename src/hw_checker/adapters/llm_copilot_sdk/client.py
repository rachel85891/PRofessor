"""CopilotSdkLLMClient: `LLMClient` over the GitHub Copilot SDK (ADR 0022).

Each call runs through the developer's Copilot seat: the SDK starts the Copilot runtime
once per client, and every call gets a new session with one model, no tools and no
permissions (see `options`). The reply is asked for as JSON with the output type's
schema, then read and validated here; an unreadable reply gets one more try with the
problems as feedback.

Retries follow ADR 0020: only rate limits, timeouts and server errors are retried, and
every attempt is counted. The prompt, the reply and the token are never logged. Each
session is deleted after its call, so no prompt stays in the Copilot home.

The client must be closed (`close()` or a `with` block), which stops the runtime process.
"""

import asyncio
import logging
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self, TypeVar

from copilot._jsonrpc import JsonRpcError, ProcessExitedError
from copilot.session_events import (
    AssistantMessageData,
    AssistantUsageData,
    SessionErrorData,
    SessionEvent,
)
from pydantic import BaseModel, SecretStr

from hw_checker.adapters.llm_copilot_sdk import failures
from hw_checker.adapters.llm_copilot_sdk.failures import AttemptError
from hw_checker.adapters.llm_copilot_sdk.options import runtime_options, session_options
from hw_checker.adapters.llm_copilot_sdk.replies import (
    UnreadableReplyError,
    feedback,
    read_reply,
    strict_schema,
)
from hw_checker.adapters.llm_copilot_sdk.runtime import (
    LoopThread,
    Runtime,
    RuntimeFactory,
    SdkRuntime,
    Session,
)
from hw_checker.domain.errors import LLMError

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=BaseModel)
Clock = Callable[[], float]
Sleep = Callable[[float], None]

MAX_BACKOFF_SECONDS = 30.0
TEMP_PREFIX = "hw-checker-copilot-"
WORK_FOLDER = "work"
HOME_FOLDER = "home"
RUNTIME_ERRORS = (JsonRpcError, RuntimeError)
"""What the SDK raises when the runtime refuses a request or a run fails."""
CONNECTION_ERRORS = (ProcessExitedError, OSError)
"""What the SDK raises when the runtime process is gone. `TimeoutError` is an
`OSError`, so it is always caught before these."""


@dataclass(frozen=True)
class CopilotClientConfig:
    """The model of each role and the call limits."""

    models: Mapping[str, str]
    """The Copilot model id for each role."""
    timeout_seconds: float
    """The longest wait for a session or one reply."""
    start_timeout_seconds: float
    """The longest wait for the runtime to start and report its login."""
    max_retries: int
    retry_backoff_seconds: float
    max_prompt_chars: int


@dataclass
class _Usage:
    """What the calls of one `complete` used, as the runtime reports it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    premium_requests: float | None = None

    def add(self, usage: AssistantUsageData) -> None:
        self.input_tokens = _sum(self.input_tokens, usage.input_tokens)
        self.output_tokens = _sum(self.output_tokens, usage.output_tokens)
        self.premium_requests = _sum(self.premium_requests, usage.cost)


class CopilotSdkLLMClient:
    """`LLMClient` that calls each role's model through the Copilot SDK, tools always off."""

    def __init__(
        self,
        config: CopilotClientConfig,
        token: SecretStr | None = None,
        runtime_factory: RuntimeFactory = SdkRuntime,
        clock: Clock = time.monotonic,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._config = config
        self._token = token.get_secret_value() if token is not None else None
        self._runtime_factory = runtime_factory
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._loop: LoopThread | None = None
        self._runtime: Runtime | None = None
        self._temp: tempfile.TemporaryDirectory[str] | None = None
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def complete(self, role: str, prompt: str, output_type: type[OutputT]) -> OutputT:
        """Send `prompt` to the model for `role` and parse the reply into `output_type`.

        Raises:
            LLMError: the role has no model, the prompt is too long, the client is closed,
                the call failed after any retries, or the reply could not be parsed into
                `output_type`.
        """
        model = self._config.models.get(role)
        if model is None:
            raise LLMError(f"No model is configured for the LLM role '{role}'.")
        if len(prompt) > self._config.max_prompt_chars:
            raise LLMError(
                f"The prompt for {role} has {len(prompt)} characters, more than the limit of "
                f"{self._config.max_prompt_chars}; it was not sent."
            )
        started = self._clock()
        attempts = 0
        outcome = "error"
        usage = _Usage()
        try:
            while True:
                attempts += 1
                try:
                    result = self._attempt(model, prompt, output_type, usage)
                except AttemptError as failure:
                    outcome = failure.outcome
                    if not failure.retryable or attempts > self._config.max_retries:
                        raise LLMError(_with_attempts(str(failure), attempts)) from None
                    self._sleep(self._backoff(attempts))
                    continue
                outcome = "ok"
                return result
        finally:
            self._log_call(role, model, outcome, self._clock() - started, attempts, usage)

    def close(self) -> None:
        """Stop the runtime process and the event loop, and remove the temporary folders.

        Calling it again does nothing.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop, runtime, temp = self._loop, self._runtime, self._temp
            self._loop, self._runtime, self._temp = None, None, None
        if loop is not None:
            if runtime is not None:
                self._stop(loop, runtime)
            loop.close()
        if temp is not None:
            temp.cleanup()

    def _attempt(
        self, model: str, prompt: str, output_type: type[OutputT], usage: _Usage
    ) -> OutputT:
        loop, runtime, work_dir = self._started()
        try:
            return loop.run(
                lambda: self._converse(runtime, work_dir, model, prompt, output_type, usage)
            )
        except AttemptError as failure:
            if failure.outcome == failures.CLI_STOPPED:
                self._forget(runtime)
            raise

    def _forget(self, runtime: Runtime) -> None:
        """Stop a runtime whose process broke, so the next call starts a new one."""
        with self._lock:
            if self._runtime is not runtime or self._loop is None:
                return
            self._runtime = None
            loop = self._loop
        self._stop(loop, runtime)

    def _started(self) -> tuple[LoopThread, Runtime, Path]:
        """The running runtime, started on the first call; later calls reuse it.

        Raises:
            LLMError: the client is closed.
            AttemptError: the runtime could not start, or it has no Copilot login.
        """
        with self._lock:
            if self._closed:
                raise LLMError("The Copilot LLM client is closed.")
            if self._loop is None:
                self._loop = LoopThread()
            if self._temp is None:
                self._temp = tempfile.TemporaryDirectory(prefix=TEMP_PREFIX)
            work_dir = Path(self._temp.name) / WORK_FOLDER
            if self._runtime is None:
                self._runtime = self._start(self._loop, Path(self._temp.name), work_dir)
            return self._loop, self._runtime, work_dir

    def _start(self, loop: LoopThread, temp: Path, work_dir: Path) -> Runtime:
        work_dir.mkdir(exist_ok=True)
        home_dir = temp / HOME_FOLDER
        home_dir.mkdir(exist_ok=True)
        try:
            runtime = self._runtime_factory(runtime_options(work_dir, home_dir, self._token))
        except (RuntimeError, ValueError, OSError) as error:
            raise failures.cli_failed_to_start(self._clean(str(error))) from None
        try:
            authenticated = loop.run(lambda: self._start_and_check_login(runtime))
        except TimeoutError:
            self._stop(loop, runtime)
            raise failures.cli_failed_to_start(
                f"no answer within {self._config.start_timeout_seconds:g} s"
            ) from None
        except (*RUNTIME_ERRORS, *CONNECTION_ERRORS) as error:
            self._stop(loop, runtime)
            raise failures.cli_failed_to_start(self._clean(str(error))) from None
        if not authenticated:
            self._stop(loop, runtime)
            raise failures.not_logged_in()
        return runtime

    async def _start_and_check_login(self, runtime: Runtime) -> bool:
        async with asyncio.timeout(self._config.start_timeout_seconds):
            await runtime.start()
            status = await runtime.get_auth_status()
        return status.isAuthenticated

    async def _converse(
        self,
        runtime: Runtime,
        work_dir: Path,
        model: str,
        prompt: str,
        output_type: type[OutputT],
        usage: _Usage,
    ) -> OutputT:
        """One attempt: a new session, the prompt, and at most one retry with feedback."""
        session = await self._open_session(runtime, work_dir, model)
        errors: list[SessionErrorData] = []
        unsubscribe = session.on(lambda event: _observe(event, errors, usage))
        try:
            content = await self._ask(session, model, prompt, output_type, errors)
            try:
                return read_reply(content, output_type)
            except UnreadableReplyError as problem:
                retry = feedback(problem)
            content = await self._ask(session, model, retry, output_type, errors)
            try:
                return read_reply(content, output_type)
            except UnreadableReplyError:
                raise failures.unparseable(model, output_type.__name__) from None
        finally:
            unsubscribe()
            await self._end_session(runtime, session)

    async def _open_session(self, runtime: Runtime, work_dir: Path, model: str) -> Session:
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                return await runtime.create_session(session_options(model, work_dir))
        except TimeoutError:
            raise failures.timed_out(model, self._config.timeout_seconds) from None
        except CONNECTION_ERRORS:
            raise failures.cli_stopped() from None
        except RUNTIME_ERRORS as error:
            raise failures.rejected(self._clean(_runtime_message(error))) from None

    async def _ask(
        self,
        session: Session,
        model: str,
        prompt: str,
        output_type: type[BaseModel],
        errors: list[SessionErrorData],
    ) -> str:
        try:
            event = await session.send_and_wait(
                prompt,
                response_schema=strict_schema(output_type),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError:
            await self._abort(session)
            raise failures.timed_out(model, self._config.timeout_seconds) from None
        except CONNECTION_ERRORS:
            raise failures.cli_stopped() from None
        except RUNTIME_ERRORS as error:
            raise self._failure(errors, model, error) from None
        if event is None or not isinstance(event.data, AssistantMessageData):
            raise self._failure(errors, model, RuntimeError("the run ended without a reply"))
        return event.data.content

    def _failure(
        self, errors: list[SessionErrorData], model: str, error: Exception
    ) -> AttemptError:
        if errors:
            last = errors[-1]
            return failures.from_session_error(last, model, self._clean(last.message))
        return failures.rejected(self._clean(_runtime_message(error)))

    async def _abort(self, session: Session) -> None:
        try:
            await session.abort()
        except (*RUNTIME_ERRORS, *CONNECTION_ERRORS) as error:
            _log_cleanup_failure("abort the session", error)

    async def _end_session(self, runtime: Runtime, session: Session) -> None:
        """Disconnect the session and delete it, so its prompt is not kept on disk."""
        try:
            await session.disconnect()
            await runtime.delete_session(session.session_id)
        except (*RUNTIME_ERRORS, *CONNECTION_ERRORS) as error:
            _log_cleanup_failure("end the session", error)

    def _stop(self, loop: LoopThread, runtime: Runtime) -> None:
        try:
            loop.run(runtime.stop)
        except (ExceptionGroup, *RUNTIME_ERRORS, *CONNECTION_ERRORS) as error:
            _log_cleanup_failure("stop the Copilot runtime", error)

    def _clean(self, text: str) -> str:
        return failures.clean_message(text, self._token)

    def _backoff(self, attempt: int) -> float:
        return float(
            min(self._config.retry_backoff_seconds * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
        )

    def _log_call(
        self, role: str, model: str, outcome: str, seconds: float, attempts: int, usage: _Usage
    ) -> None:
        logger.info(
            "LLM call for %s (copilot %s): %s in %.1f s, %s input and %s output tokens, "
            "%s premium requests, %d attempts",
            role,
            model,
            outcome,
            seconds,
            usage.input_tokens,
            usage.output_tokens,
            usage.premium_requests,
            attempts,
            extra={
                "llm_provider": "copilot",
                "llm_role": role,
                "llm_model": model,
                "outcome": outcome,
                "duration_seconds": round(seconds, 3),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "premium_requests": usage.premium_requests,
                "attempts": attempts,
            },
        )


def _observe(event: SessionEvent, errors: list[SessionErrorData], usage: _Usage) -> None:
    """Keep the errors and the usage the runtime reports for the session's own turns."""
    if event.agent_id:
        return
    if isinstance(event.data, SessionErrorData):
        errors.append(event.data)
    elif isinstance(event.data, AssistantUsageData):
        usage.add(event.data)


def _runtime_message(error: Exception) -> str:
    return error.message if isinstance(error, JsonRpcError) else str(error)


def _log_cleanup_failure(action: str, error: BaseException) -> None:
    logger.warning(
        "Could not %s: %s", action, type(error).__name__, extra={"error_type": type(error).__name__}
    )


def _sum[N: (int, float)](total: N | None, value: N | None) -> N | None:
    if value is None:
        return total
    return value if total is None else total + value


def _with_attempts(message: str, attempts: int) -> str:
    if attempts == 1:
        return message
    return f"{message.removesuffix('.')} (after {attempts} attempts)."
