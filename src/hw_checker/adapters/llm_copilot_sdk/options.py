"""The options every Copilot runtime and session is created with: text in, text out.

The Copilot runtime is an agent that can run shell commands, edit files and call MCP
servers. A homework checker only needs a reply, so every session gets an empty tool
allowlist, no MCP servers, a permission handler that rejects every request, an empty
temporary working directory, and every feature that reads the machine's configuration
or keeps state between sessions switched off (ADR 0022).
"""

from pathlib import Path
from typing import Any

from copilot import PermissionRequest
from copilot.rpc import PermissionDecisionReject

PERMISSION_REFUSAL = "Tools are disabled for this session; answer with text only."
RUNTIME_LOG_LEVEL = "error"
BUILTIN_MCP_SERVERS = ("github-mcp-server",)
"""The MCP servers the runtime starts by itself, even with no `mcp_servers` given. The
built-in GitHub server connected with the logged-in account and 19 tools (2026-11-02,
runtime 1.0.105); the integration test fails if any MCP server connects."""


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
        "system_message": {
            "mode": "customize",
            "sections": {"environment_context": {"action": "remove"}},
        },
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
