"""The runtime and session options: no tools, no permissions, nothing from the machine."""

from pathlib import Path

import pytest
from copilot import PermissionRequest
from copilot.rpc import PermissionDecisionReject
from copilot.session_events import PermissionRequestRead, PermissionRequestShell

from hw_checker.adapters.llm_copilot_sdk.options import (
    deny_every_permission,
    runtime_options,
    session_options,
)

TOKEN = "github_pat_not_a_real_token"  # pragma: allowlist secret


def test_a_session_has_no_tools_no_mcp_servers_and_only_its_model(tmp_path: Path) -> None:
    options = session_options("claude-haiku-5.5", tmp_path)

    assert options["model"] == "claude-haiku-5.5"
    assert options["available_tools"] == []
    assert options["mcp_servers"] == {}
    assert options["disabled_mcp_servers"] == ["github-mcp-server"]
    assert options["working_directory"] == str(tmp_path)
    assert options["on_permission_request"] is deny_every_permission
    assert "tools" not in options
    assert "hooks" not in options
    assert "custom_agents" not in options


def test_a_session_reads_no_configuration_and_keeps_no_state(tmp_path: Path) -> None:
    options = session_options("model", tmp_path)

    assert options["system_message"] == {
        "mode": "customize",
        "sections": {"environment_context": {"action": "remove"}},
    }
    switched_off = (
        "enable_config_discovery",
        "enable_on_demand_instruction_discovery",
        "enable_skills",
        "enable_file_hooks",
        "enable_host_git_operations",
        "enable_session_store",
        "enable_session_telemetry",
        "enable_experimental_mode",
        "coauthor_enabled",
        "manage_schedule_enabled",
        "streaming",
    )
    assert {name: options[name] for name in switched_off} == dict.fromkeys(switched_off, False)
    assert options["skip_custom_instructions"] is True
    assert options["skip_embedding_retrieval"] is True
    assert options["custom_agents_local_only"] is True
    assert options["included_builtin_skills"] == []
    assert options["memory"] == {"enabled": False}
    assert options["embedding_cache_storage"] == "in-memory"
    assert options["mcp_oauth_token_storage"] == "in-memory"


@pytest.mark.parametrize(
    "request_",
    [
        PermissionRequestShell(
            can_offer_session_approval=False,
            commands=[],
            full_command_text="dir",
            has_write_file_redirection=False,
            intention="list the files",
            possible_paths=[],
            possible_urls=[],
        ),
        PermissionRequestRead(intention="read a file", path="C:/secret.txt"),
    ],
)
def test_every_permission_request_is_rejected(request_: PermissionRequest) -> None:
    decision = deny_every_permission(request_, {})

    assert isinstance(decision, PermissionDecisionReject)


def test_without_a_token_the_runtime_uses_the_logged_in_user(tmp_path: Path) -> None:
    options = runtime_options(tmp_path / "work", tmp_path / "home", None)

    assert options == {
        "working_directory": str(tmp_path / "work"),
        "log_level": "error",
        "use_logged_in_user": True,
    }


def test_with_a_token_the_runtime_is_isolated_and_gets_the_token(tmp_path: Path) -> None:
    options = runtime_options(tmp_path / "work", tmp_path / "home", TOKEN)

    assert options == {
        "working_directory": str(tmp_path / "work"),
        "log_level": "error",
        "mode": "empty",
        "base_directory": str(tmp_path / "home"),
        "github_token": TOKEN,
        "use_logged_in_user": False,
    }
