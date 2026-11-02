"""The Copilot runtime as the client sees it, and the thread its event loop runs on.

`Runtime` and `Session` are the small part of the SDK the client uses, so unit tests
replace the SDK with a fake and never start the real runtime.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, TypeVar

from copilot import CopilotClient, GetAuthStatusResponse
from copilot.session_events import SessionEvent

T = TypeVar("T")


class Session(Protocol):
    """One conversation with one model."""

    @property
    def session_id(self) -> str: ...

    def on(self, handler: Callable[[SessionEvent], None]) -> Callable[[], None]:
        """Call `handler` with every event of the session; returns the unsubscribe call."""
        ...

    async def send_and_wait(
        self, prompt: str, *, response_schema: dict[str, Any], timeout: float
    ) -> SessionEvent | None:
        """Send `prompt` and return the final assistant message event."""
        ...

    async def abort(self) -> None: ...

    async def disconnect(self) -> None: ...


class Runtime(Protocol):
    """The Copilot runtime process and its sessions."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def get_auth_status(self) -> GetAuthStatusResponse: ...

    async def create_session(self, options: dict[str, Any]) -> Session: ...

    async def delete_session(self, session_id: str) -> None: ...


RuntimeFactory = Callable[[dict[str, Any]], Runtime]
"""Creates a runtime from the keyword arguments for `copilot.CopilotClient`."""


class SdkRuntime:
    """`Runtime` over the SDK's `CopilotClient`, which starts the bundled runtime process."""

    def __init__(self, options: dict[str, Any]) -> None:
        self._client = CopilotClient(**options)

    async def start(self) -> None:
        await self._client.start()

    async def stop(self) -> None:
        await self._client.stop()

    async def get_auth_status(self) -> GetAuthStatusResponse:
        return await self._client.get_auth_status()

    async def create_session(self, options: dict[str, Any]) -> Session:
        return await self._client.create_session(**options)

    async def delete_session(self, session_id: str) -> None:
        await self._client.delete_session(session_id)


class LoopThread:
    """An asyncio event loop on its own daemon thread, so synchronous callers on any
    thread can run the SDK's coroutines on one loop."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="copilot-sdk-loop", daemon=True
        )
        self._thread.start()

    def run(self, work: Callable[[], Coroutine[Any, Any, T]]) -> T:
        """Run the coroutine `work()` on the loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(work(), self._loop).result()

    def close(self) -> None:
        """Stop the loop and its thread; nothing may run on it afterwards."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
