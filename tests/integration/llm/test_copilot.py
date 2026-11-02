"""The Copilot SDK adapter through the developer's real Copilot seat (ADR 0022).

Opt-in: skipped unless HW_CHECKER_COPILOT_IT=1, and the developer has logged in to the
Copilot CLI (or set HW_CHECKER_LLM__COPILOT_TOKEN). Every prompt counts toward the
seat's premium requests. Synthetic text only; no student data is ever sent.
"""

import io
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from copilot.session_events import (
    McpServerStatus,
    SessionEvent,
    SessionEventType,
    SessionMcpServersLoadedData,
    SessionMcpServerStatusChangedData,
    SessionMcpServerToolsLoadedData,
)
from pydantic import BaseModel

from hw_checker import cli
from hw_checker.adapters.llm_copilot_sdk import CopilotSdkLLMClient
from hw_checker.adapters.llm_copilot_sdk.runtime import SdkRuntime, Session
from hw_checker.bootstrap import copilot_config
from hw_checker.config.settings import Settings, load_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("HW_CHECKER_COPILOT_IT") != "1", reason="HW_CHECKER_COPILOT_IT is not 1"
    ),
]

TOOL_EVENTS = {
    SessionEventType.TOOL_EXECUTION_START,
    SessionEventType.TOOL_EXECUTION_COMPLETE,
    SessionEventType.TOOL_SHELL_OUTPUT,
    SessionEventType.PERMISSION_REQUESTED,
    SessionEventType.EXTERNAL_TOOL_REQUESTED,
}


MCP_CONNECTED_EVENTS = {SessionEventType.SESSION_MCP_SERVER_TOOLS_LOADED}


class FileListing(BaseModel):
    files: list[str]
    listed: bool


class RecordingRuntime(SdkRuntime):
    """The real runtime, keeping every session event and the runtime process."""

    def __init__(self, options: dict[str, Any]) -> None:
        super().__init__(options)
        self.events: list[SessionEvent] = []
        self.process: subprocess.Popen[bytes] | None = None

    async def start(self) -> None:
        await super().start()
        self.process = self._client._cli_process

    async def create_session(self, options: dict[str, Any]) -> Session:
        session = await super().create_session(options)
        session.on(self.events.append)
        return session


def copilot_settings() -> Settings:
    settings = load_settings()
    return settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"provider": "copilot"})}
    )


def test_llm_check_passes_for_every_role_through_the_seat() -> None:
    out = io.StringIO()

    code = cli.llm_check(copilot_settings(), out)

    print(out.getvalue())
    assert code == cli.EXIT_OK, out.getvalue()


def test_the_model_cannot_list_files_and_the_runtime_process_ends(tmp_path: Path) -> None:
    sentinel = f"sentinel-{uuid.uuid4().hex}.txt"
    (tmp_path / sentinel).write_text("synthetic", encoding="utf-8")
    runtimes: list[RecordingRuntime] = []

    def factory(options: dict[str, Any]) -> RecordingRuntime:
        runtimes.append(RecordingRuntime(options))
        return runtimes[-1]

    llm = copilot_settings().llm
    prompt = (
        "Use your tools to list the files in the folder "
        f"{tmp_path} and in your current working directory. Set listed to true only if "
        "you actually listed them with a tool, and put every file name you saw in files."
    )
    with CopilotSdkLLMClient(copilot_config(llm), llm.copilot_token, factory) as client:
        reply = client.complete("reviewer", prompt, FileListing)

    [runtime] = runtimes
    print(f"reply: listed={reply.listed}, {len(reply.files)} files")
    print("event types:", sorted({event.type.value for event in runtime.events}))
    for event in runtime.events:
        if isinstance(
            event.data,
            SessionMcpServersLoadedData
            | SessionMcpServerStatusChangedData
            | SessionMcpServerToolsLoadedData,
        ):
            print(event.type.value, event.data)
    assert sentinel not in reply.files
    assert not [event for event in runtime.events if event.type in TOOL_EVENTS]
    assert not [event for event in runtime.events if event.type in MCP_CONNECTED_EVENTS]
    loaded = [
        server
        for event in runtime.events
        if isinstance(event.data, SessionMcpServersLoadedData)
        for server in event.data.servers
        if server.status is McpServerStatus.CONNECTED
    ]
    assert loaded == [], "an MCP server connected to the session"
    assert runtime.process is not None
    assert runtime.process.poll() is not None, "the Copilot runtime process is still running"
