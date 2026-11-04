"""The options every Copilot runtime and session is created with: text in, text out.

The Copilot runtime is an agent that can run shell commands, edit files and call MCP
servers. A homework checker only needs a reply, so every session gets an empty tool
allowlist, no MCP servers, a permission handler that rejects every request, an empty
temporary working directory, and every feature that reads the machine's configuration
or keeps state between sessions switched off (ADR 0022). The runtime's coding-agent
sections of the system prompt are removed too: they conflict with a structured reply.
"""

from pathlib import Path
from typing import Any

from copilot import PermissionRequest
from copilot.rpc import PermissionDecisionReject
from copilot.session import SYSTEM_MESSAGE_SECTIONS

from hw_checker.domain.errors import LLMError

PERMISSION_REFUSAL = "Tools are disabled for this session; answer with text only."
RUNTIME_LOG_LEVEL = "error"
BUILTIN_MCP_SERVERS = ("github-mcp-server",)
"""The MCP servers the runtime starts by itself, even with no `mcp_servers` given. The
built-in GitHub server connected with the logged-in account and 19 tools (2026-11-02,
runtime 1.0.105); the integration test fails if any MCP server connects."""
REMOVED_SECTIONS = (
    # The machine's working directory, OS and file listing: none of the session's business.
    "environment_context",
    # "Limit your response to 100 words or less": a full review plan is far longer; with
    # it, about half the fixer's plans on a real repo came back as one placeholder edit
    # (stage 14c, ADR 0022).
    "tone",
    # Parallel tool calls and batching: sessions have no tools.
    "tool_efficiency",
    # "Only comment code that needs a bit of clarification": the review's //fix: lines and
    # notes are comments by design.
    "code_change_rules",
    # "Ask for guidance if uncertain": a session gets one prompt and must answer it.
    "guidelines",
)
"""The runtime's system-prompt sections every session removes; `safety` and the identity
preamble stay."""


def deny_every_permission(
    request: PermissionRequest, invocation: object
) -> PermissionDecisionReject:
    """Reject every permission request (shell, file, URL, MCP); no tool may ever run."""
    return PermissionDecisionReject(feedback=PERMISSION_REFUSAL)


def runtime_options(work_dir: Path, home_dir: Path, token: str | None) -> dict[str, Any]:
    """The keyword arguments for `copilot.CopilotClient`.

    The runtime process starts in `work_dir`, an empty temporary directory. With a token
    the runtime runs in the SDK's `empty` mode: its own Copilot home in `home_dir`, no
    keychain, no installed plugins; the SDK passes the token to the runtime process only
    through its environment. Without a token it uses the Copilot CLI's logged-in user,
    which needs the user's own Copilot home and keychain.
    """
    options: dict[str, Any] = {
        "working_directory": str(work_dir),
        "log_level": RUNTIME_LOG_LEVEL,
    }
    if token is None:
        options["use_logged_in_user"] = True
        return options
    options |= {
        "mode": "empty",
        "base_directory": str(home_dir),
        "github_token": token,
        "use_logged_in_user": False,
    }
    return options


def session_options(model: str, work_dir: Path) -> dict[str, Any]:
    """The keyword arguments for `CopilotClient.create_session`: one model, no tools.

    Every value is set explicitly, whatever the runtime's mode, so no default of the SDK
    or the runtime can turn a tool or a configuration source back on.
    """
    return {
        "model": model,
        "available_tools": [],
        "mcp_servers": {},
        "disabled_mcp_servers": list(BUILTIN_MCP_SERVERS),
        "on_permission_request": deny_every_permission,
        "working_directory": str(work_dir),
        "system_message": system_message(REMOVED_SECTIONS),
        "streaming": False,
        "enable_config_discovery": False,
        "skip_custom_instructions": True,
        "enable_on_demand_instruction_discovery": False,
        "enable_skills": False,
        "included_builtin_skills": [],
        "enable_file_hooks": False,
        "enable_host_git_operations": False,
        "enable_session_store": False,
        "enable_session_telemetry": False,
        "enable_experimental_mode": False,
        "custom_agents_local_only": True,
        "memory": {"enabled": False},
        "coauthor_enabled": False,
        "manage_schedule_enabled": False,
        "skip_embedding_retrieval": True,
        "embedding_cache_storage": "in-memory",
        "mcp_oauth_token_storage": "in-memory",
    }


def system_message(removed: tuple[str, ...]) -> dict[str, Any]:
    """The system-message customization that removes the `removed` sections.

    The runtime silently ignores a section name it does not know (runtime 1.0.105), so
    every name is checked against the SDK's own list first.

    Raises:
        LLMError: a name is not a section the SDK knows.
    """
    unknown = [name for name in removed if name not in SYSTEM_MESSAGE_SECTIONS]
    if unknown:
        raise LLMError(
            f"The Copilot SDK has no system-message section {', '.join(unknown)}; the "
            "session would keep that section, so it is not created."
        )
    return {"mode": "customize", "sections": {name: {"action": "remove"} for name in removed}}
