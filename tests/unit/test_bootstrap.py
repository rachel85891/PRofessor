import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from hw_checker import bootstrap
from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.github_api import GitHubCodeHost
from hw_checker.adapters.llm_copilot_sdk import CopilotSdkLLMClient
from hw_checker.adapters.llm_openai_compatible import OpenAICompatibleLLMClient
from hw_checker.cli import LlmCheckReply
from hw_checker.config.errors import ConfigurationError
from hw_checker.config.settings import (
    REPO_ROOT,
    CopilotSettings,
    FixerSettings,
    GitHubSettings,
    LlmSettings,
    RunnerSettings,
    Settings,
    StorageSettings,
)
from hw_checker.domain import (
    AssignmentResult,
    ExecutionResult,
    FailureKind,
    NotEvaluated,
    NotEvaluatedReason,
    RepoRunResult,
    RepoRunStatus,
    RosterWarning,
    RosterWarningKind,
    RunStatus,
    RunSummary,
    Severity,
    StepStatus,
)
from hw_checker.domain.errors import LLMError
from hw_checker.ports import LLMClient
from hw_checker.ports.llm import TokenUsage
from tests.fakes.llm import FakeLLMClient
from tests.unit.domain import builders

FAKE_KEY = "not-a-real-key"  # pragma: allowlist secret


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"LANGSMITH_TRACING": "false"},
        {"LANGSMITH_TRACING": "", "LANGSMITH_API_KEY": "  "},
    ],
    ids=["nothing-set", "tracing-false", "blank"],
)
def test_tracing_off_is_accepted(environ: dict[str, str]) -> None:
    bootstrap.require_tracing_off(environ)


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        ({"LANGSMITH_TRACING": "true"}, "LANGSMITH_TRACING"),
        ({"LANGCHAIN_TRACING_V2": "1"}, "LANGCHAIN_TRACING_V2"),
        ({"LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": FAKE_KEY}, "LANGSMITH_API_KEY"),
        ({"LANGCHAIN_API_KEY": FAKE_KEY}, "LANGCHAIN_API_KEY"),
    ],
    ids=["langsmith-tracing", "langchain-tracing", "langsmith-key", "langchain-key"],
)
def test_tracing_switch_or_key_is_refused(environ: dict[str, str], named: str) -> None:
    with pytest.raises(ConfigurationError, match=f"tracing must stay off.*{named}"):
        bootstrap.require_tracing_off(environ)


def test_studio_graph_refuses_to_build_with_tracing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    bootstrap.studio_graph.cache_clear()

    with pytest.raises(ConfigurationError):
        bootstrap.studio_graph()


def test_llm_client_needs_the_api_key() -> None:
    with pytest.raises(ConfigurationError, match="HW_CHECKER_LLM__API_KEY"):
        bootstrap.build_openrouter_client(Settings(llm=LlmSettings()))


def test_llm_client_is_built_with_the_api_key() -> None:
    settings = Settings(llm=LlmSettings(api_key=SecretStr(FAKE_KEY)))

    client = bootstrap.build_openrouter_client(settings)

    assert isinstance(client, OpenAICompatibleLLMClient)


def test_llm_calls_trust_the_system_certificate_store(monkeypatch: pytest.MonkeyPatch) -> None:
    real_default_context = ssl.create_default_context
    system_context = real_default_context()

    def default_context(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        # Only the no-argument call is the system store; the SDK's own calls pass cafile.
        return real_default_context(*args, **kwargs) if args or kwargs else system_context

    monkeypatch.setattr(ssl, "create_default_context", default_context)
    built: list[dict[str, Any]] = []

    class RecordingClient(httpx2.Client):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx2, "Client", RecordingClient)
    settings = Settings(llm=LlmSettings(api_key=SecretStr(FAKE_KEY), timeout_seconds=45))

    bootstrap.build_openrouter_client(settings)

    assert built == [{"verify": system_context, "timeout": 45}]


def test_github_config_maps_the_account_and_limits() -> None:
    github = GitHubSettings(account="review-bot", fork_prefix="fork_", fork_wait_seconds=9)

    config = bootstrap.github_config(github)

    assert (config.account, config.fork_prefix, config.fork_wait_seconds) == (
        "review-bot",
        "fork_",
        9,
    )
    assert (config.api_url, config.web_url) == (github.api_url, github.web_url)


def test_publishing_clients_are_built_without_a_token_too() -> None:
    settings = Settings()

    assert isinstance(bootstrap.build_code_host(settings), GitHubCodeHost)
    assert isinstance(bootstrap.build_publishing_git(settings), LocalGitClient)


def test_fixer_config_maps_the_limits_and_salts_the_cache_with_the_models() -> None:
    settings = Settings(fixer=FixerSettings(max_attempts=3, max_fix_lines=4))

    config = bootstrap.fixer_config(settings)

    assert (config.max_attempts, config.max_fix_lines) == (3, 4)
    assert config.max_prompt_chars == settings.llm.max_prompt_chars
    other_models = settings.model_copy(
        update={
            "llm": settings.llm.model_copy(update={"roles": {**settings.llm.roles, "fixer": "x/y"}})
        }
    )
    assert bootstrap.fixer_config(other_models).cache_salt != config.cache_salt
    assert bootstrap.fixer_config(settings).cache_salt == config.cache_salt


def test_the_fixer_cache_lives_in_the_workspace_only_when_the_fixer_is_on(tmp_path: Path) -> None:
    off = Settings(workspace_dir=tmp_path)
    on = Settings(
        workspace_dir=tmp_path,
        fixer=FixerSettings(enabled=True),
        llm=LlmSettings(student_code_approved=True),
    )

    assert bootstrap.fixer_cache(off) is None
    assert bootstrap.fixer_cache(on) is not None
    assert (tmp_path / "fixer_cache.sqlite").is_file()


class CountingClient:
    """An LLM client that counts how often it is opened and closed."""

    opened = 0
    closed = 0

    def __enter__(self) -> "CountingClient":
        CountingClient.opened += 1
        return self

    def __exit__(self, *exc: object) -> None:
        CountingClient.closed += 1

    def complete(self, role: str, prompt: str, output_type: type[Any]) -> Any:
        return output_type(answer="pong")

    def complete_with_usage(self, role: str, prompt: str, output_type: type[Any]) -> Any:
        return output_type(answer="pong"), TokenUsage()


@pytest.fixture
def counting(monkeypatch: pytest.MonkeyPatch) -> type[CountingClient]:
    CountingClient.opened = CountingClient.closed = 0

    @contextmanager
    def open_client(settings: Settings) -> Iterator[CountingClient]:
        with CountingClient() as client:
            yield client

    monkeypatch.setattr(bootstrap, "open_llm_client", open_client)
    return CountingClient


class Pong(BaseModel):
    answer: str


FIXER_ON = Settings(
    fixer=FixerSettings(enabled=True),
    llm=LlmSettings(provider="copilot", student_code_approved=True),
)


def test_no_fixer_client_when_the_fixer_is_off(counting: type[CountingClient]) -> None:
    with bootstrap.open_fixer_llm(Settings()) as client:
        assert client is None

    assert counting.opened == 0


def test_the_fixer_client_opens_on_its_first_call_once_and_always_closes(
    counting: type[CountingClient],
) -> None:
    with bootstrap.open_fixer_llm(FIXER_ON) as client:
        assert client is not None
        assert counting.opened == 0
        client.complete("fixer", "p", Pong)
        client.complete_with_usage("judge", "p", Pong)

    assert (counting.opened, counting.closed) == (1, 1)


def test_the_fixer_client_closes_when_the_round_fails(counting: type[CountingClient]) -> None:
    with pytest.raises(RuntimeError), bootstrap.open_fixer_llm(FIXER_ON) as client:
        assert client is not None
        client.complete("fixer", "p", Pong)
        raise RuntimeError("the round failed")

    assert (counting.opened, counting.closed) == (1, 1)


def test_a_fixer_client_that_was_never_called_starts_nothing(
    counting: type[CountingClient],
) -> None:
    with bootstrap.open_fixer_llm(FIXER_ON):
        pass

    assert (counting.opened, counting.closed) == (0, 0)


def test_the_openrouter_fixer_without_a_key_fails_before_the_round(
    counting: type[CountingClient],
) -> None:
    settings = FIXER_ON.model_copy(
        update={"llm": FIXER_ON.llm.model_copy(update={"provider": "openrouter"})}
    )

    with (
        pytest.raises(ConfigurationError, match="HW_CHECKER_LLM__API_KEY"),
        bootstrap.open_fixer_llm(settings),
    ):
        pass


def test_llm_config_maps_every_role_and_limit() -> None:
    llm = LlmSettings(max_retries=4, max_prompt_chars=99, max_output_tokens=512)

    config = bootstrap.llm_config(llm)

    assert dict(config.models) == dict(llm.roles)
    assert (config.base_url, config.max_retries, config.max_prompt_chars) == (
        llm.base_url,
        4,
        99,
    )
    assert config.extra_body == llm.extra_body
    assert (config.temperature, config.max_output_tokens) == (None, 512)


def test_openrouter_is_the_default_provider() -> None:
    settings = Settings(llm=LlmSettings(api_key=SecretStr(FAKE_KEY)))

    with bootstrap.open_llm_client(settings) as client:
        assert isinstance(client, OpenAICompatibleLLMClient)


def test_openrouter_without_a_key_fails_before_the_block_runs() -> None:
    with (
        pytest.raises(ConfigurationError, match="HW_CHECKER_LLM__API_KEY"),
        bootstrap.open_llm_client(Settings(llm=LlmSettings())),
    ):
        pytest.fail("the block must not run")


def test_the_copilot_provider_opens_the_copilot_client_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[CopilotSdkLLMClient] = []
    monkeypatch.setattr(CopilotSdkLLMClient, "close", lambda self: closed.append(self))
    settings = Settings(llm=LlmSettings(provider="copilot"))

    with bootstrap.open_llm_client(settings) as client:
        assert isinstance(client, CopilotSdkLLMClient)
        assert closed == []

    assert closed == [client]


def test_the_copilot_client_is_closed_when_the_block_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[CopilotSdkLLMClient] = []
    monkeypatch.setattr(CopilotSdkLLMClient, "close", lambda self: closed.append(self))
    settings = Settings(llm=LlmSettings(provider="copilot"))

    with pytest.raises(LLMError), bootstrap.open_llm_client(settings):
        raise LLMError("a role failed")

    assert len(closed) == 1


def test_the_copilot_provider_needs_no_api_key() -> None:
    settings = Settings(llm=LlmSettings(provider="copilot"))

    with bootstrap.open_llm_client(settings) as client:
        assert isinstance(client, CopilotSdkLLMClient)


def test_the_author_limit_reaches_the_clients_and_a_long_writer_prompt_is_refused() -> None:
    llm = LlmSettings(
        role_max_prompt_chars={"author": 150000}, role_max_output_tokens={"author": 16000}
    )

    openrouter = bootstrap.llm_config(llm)
    copilot = bootstrap.copilot_config(llm)

    assert openrouter.prompt_limit("author") == copilot.prompt_limit("author") == 150000
    assert openrouter.prompt_limit("writer") == copilot.prompt_limit("writer") == 60000
    assert (openrouter.output_tokens("author"), openrouter.output_tokens("writer")) == (
        16000,
        4096,
    )
    client = OpenAICompatibleLLMClient(openrouter, SecretStr("test-key"))
    with pytest.raises(LLMError, match="60001 characters, more than the limit of 60000"):
        client.complete("writer", "x" * 60001, LlmCheckReply)


def test_copilot_config_maps_the_copilot_roles_and_the_shared_limits() -> None:
    llm = LlmSettings(
        provider="copilot",
        copilot=CopilotSettings(
            roles={"reviewer": "a", "judge": "b", "fixer": "b", "writer": "a", "author": "b"},
            start_timeout_seconds=30,
        ),
        timeout_seconds=90,
        max_retries=1,
        retry_backoff_seconds=3,
        max_prompt_chars=99,
    )

    config = bootstrap.copilot_config(llm)

    assert dict(config.models) == {
        "reviewer": "a",
        "judge": "b",
        "fixer": "b",
        "writer": "a",
        "author": "b",
    }
    assert (config.timeout_seconds, config.start_timeout_seconds) == (90, 30)
    assert (config.max_retries, config.retry_backoff_seconds, config.max_prompt_chars) == (
        1,
        3,
        99,
    )


def test_the_real_client_and_the_fake_satisfy_the_port() -> None:
    copilot = CopilotSdkLLMClient(bootstrap.copilot_config(LlmSettings()))
    clients: list[LLMClient] = [
        bootstrap.build_openrouter_client(Settings(llm=LlmSettings(api_key=SecretStr(FAKE_KEY)))),
        copilot,
        FakeLLMClient(),
    ]
    copilot.close()
    assert all(callable(client.complete) for client in clients)


def test_runner_config_maps_sizes_and_images() -> None:
    settings = RunnerSettings()

    config = bootstrap.runner_config(settings)

    assert set(config.framework_images) == set(settings.framework_images)
    assert config.framework_images["net10.0"].sdk == settings.framework_images["net10.0"].sdk
    assert config.max_archive_bytes == settings.max_archive_mb * 1024 * 1024
    assert config.max_response_bytes == settings.max_response_kb * 1024
    assert config.run_limits.memory_mb == settings.run_limits.memory_mb
    assert config.label_prefix == settings.label_prefix


def rich_repo_run() -> RepoRunResult:
    return RepoRunResult(
        repo_run_id="run-1/dana-api",
        repo=builders.repo_target(),
        commit_sha="0f6db0d",
        status=RepoRunStatus.DONE,
        execution=ExecutionResult(
            build_status=StepStatus.FAILED,
            run_status=StepStatus.SKIPPED,
            failure_kind=FailureKind.BUILD_FAILED,
        ),
        assignment_results=(
            AssignmentResult(
                assignment_id="hw03",
                findings=(builders.finding(severity=Severity.CRITICAL),),
                not_evaluated=(
                    NotEvaluated(requirement_id="hw03-r1", reason=NotEvaluatedReason.BUILD_FAILED),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "value",
    [
        rich_repo_run(),
        RunSummary(run_id="run-1", assignment_id="hw01", status=RunStatus.COMPLETED),
        RosterWarning(kind=RosterWarningKind.MISSING_PARTNER, message="Row 3: no partner", row=3),
        [builders.repo_target(), builders.repo_target(repo_id="other")],
    ],
    ids=["repo-run", "summary", "roster-warning", "repo-list"],
)
def test_checkpoint_serializer_reads_back_domain_state(value: object) -> None:
    serializer = bootstrap.checkpoint_serializer()

    assert serializer.loads_typed(serializer.dumps_typed(value)) == value


def test_checkpoint_serializer_does_not_rebuild_other_types() -> None:
    class Outside(BaseModel):
        x: int = 1

    serializer = bootstrap.checkpoint_serializer()

    assert serializer.loads_typed(serializer.dumps_typed(Outside())) == {"x": 1}


def test_round_graph_from_settings_has_a_node_per_assignment_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "_connect_docker", lambda runner: object())
    settings = Settings(
        workspace_dir=tmp_path / "workspace",
        assignments_dir=REPO_ROOT / "assignments",
        storage=StorageSettings(database_url=f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"),
        max_parallel_repos=4,
    )

    graph = bootstrap.build_round_graph_from_settings(settings)

    nodes = graph.get_graph(xray=True).nodes
    assert "check_repo:assignment__general:deterministic_checks" in nodes
    assert "check_repo:assignment_hw01:deterministic_checks" in nodes
    assert graph.config is not None and graph.config.get("max_concurrency") == 4
    assert (tmp_path / "db.sqlite").is_file()


def test_open_checkpointer_uses_a_file_in_the_workspace(tmp_path: Path) -> None:
    settings = Settings(workspace_dir=tmp_path / "workspace")

    with bootstrap.open_checkpointer(settings) as checkpointer:
        assert checkpointer.get_tuple({"configurable": {"thread_id": "none"}}) is None

    assert (tmp_path / "workspace" / "checkpoints.sqlite").is_file()
