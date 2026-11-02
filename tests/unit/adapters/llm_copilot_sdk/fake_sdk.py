"""An in-memory stand-in for the Copilot SDK's runtime and sessions; never a real call."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from copilot import GetAuthStatusResponse
from copilot._jsonrpc import JsonRpcError
from copilot.session_events import (
    AssistantMessageData,
    AssistantUsageData,
    SessionErrorData,
    SessionEvent,
    SessionEventData,
    SessionEventType,
)

Step = str | SessionErrorData | BaseException | None
"""What one `send_and_wait` does: reply with this text, report this session error and
fail, raise this exception, or end without a reply."""


def event(data: SessionEventData, kind: SessionEventType) -> SessionEvent:
    return SessionEvent(data=data, id=uuid.uuid4(), timestamp=datetime.now(UTC), type=kind)


@dataclass
class Sent:
    prompt: str
    response_schema: dict[str, Any]
    timeout: float


class FakeSession:
    def __init__(self, copilot: "FakeCopilot", session_id: str) -> None:
        self._copilot = copilot
        self._handlers: list[Callable[[SessionEvent], None]] = []
        self.session_id = session_id
        self.sent: list[Sent] = []
        self.aborted = False
        self.disconnected = False

    def on(self, handler: Callable[[SessionEvent], None]) -> Callable[[], None]:
        self._handlers.append(handler)
        return lambda: self._handlers.remove(handler)

    def emit(self, data: SessionEventData, kind: SessionEventType) -> None:
        for handler in list(self._handlers):
            handler(event(data, kind))

    async def send_and_wait(
        self, prompt: str, *, response_schema: dict[str, Any], timeout: float
    ) -> SessionEvent | None:
        self.sent.append(Sent(prompt, response_schema, timeout))
        step = self._copilot.next_step()
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, SessionErrorData):
            self.emit(step, SessionEventType.SESSION_ERROR)
            raise JsonRpcError(-32603, "the run failed")
        if step is None:
            return None
        usage = AssistantUsageData(
            model="fake-model", input_tokens=100, output_tokens=7, cost=self._copilot.cost
        )
        self.emit(usage, SessionEventType.ASSISTANT_USAGE)
        return event(
            AssistantMessageData(content=step, message_id=str(uuid.uuid4())),
            SessionEventType.ASSISTANT_MESSAGE,
        )

    async def abort(self) -> None:
        self.aborted = True

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeRuntime:
    def __init__(self, copilot: "FakeCopilot", options: dict[str, Any]) -> None:
        self._copilot = copilot
        self.options = options
        self.started = False
        self.stops = 0
        self.sessions: list[tuple[dict[str, Any], FakeSession]] = []
        self.deleted: list[str] = []

    async def start(self) -> None:
        if self._copilot.start_error is not None:
            raise self._copilot.start_error
        await asyncio.sleep(self._copilot.start_delay)
        self.started = True

    async def stop(self) -> None:
        self.stops += 1

    async def get_auth_status(self) -> GetAuthStatusResponse:
        return GetAuthStatusResponse(isAuthenticated=self._copilot.authenticated)

    async def create_session(self, options: dict[str, Any]) -> FakeSession:
        if self._copilot.create_error is not None:
            raise self._copilot.create_error
        await asyncio.sleep(self._copilot.create_delay)
        session = FakeSession(self._copilot, f"session-{len(self.sessions) + 1}")
        self.sessions.append((options, session))
        return session

    async def delete_session(self, session_id: str) -> None:
        self.deleted.append(session_id)


@dataclass
class FakeCopilot:
    """Scripts the runtime: `steps` answer the sends in order (the last one repeats)."""

    steps: list[Step] = field(default_factory=lambda: ['{"answer": "pong"}'])
    authenticated: bool = True
    factory_error: Exception | None = None
    start_error: BaseException | None = None
    create_error: BaseException | None = None
    start_delay: float = 0
    create_delay: float = 0
    cost: float = 1.0
    runtimes: list[FakeRuntime] = field(default_factory=list)

    def factory(self, options: dict[str, Any]) -> FakeRuntime:
        if self.factory_error is not None:
            raise self.factory_error
        runtime = FakeRuntime(self, options)
        self.runtimes.append(runtime)
        return runtime

    def next_step(self) -> Step:
        return self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]

    def sessions(self) -> list[FakeSession]:
        return [session for runtime in self.runtimes for _, session in runtime.sessions]

    def session_options(self) -> list[dict[str, Any]]:
        return [options for runtime in self.runtimes for options, _ in runtime.sessions]
