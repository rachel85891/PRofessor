"""Composition root: the only place that creates adapters and wires them into services.

Entry points (the CLI, the LangGraph Studio factory) build the round graph from here.
"""

import functools
import importlib
import os
import pkgutil
import sqlite3
import ssl
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import Enum

import httpx2
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Checkpointer
from pydantic import BaseModel

import hw_checker.domain
from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.adapters.docker_runner.engine import EngineError, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.runner_config import (
    ContainerLimits,
    DockerRunnerConfig,
)
from hw_checker.adapters.docker_runner.runner_config import (
    FrameworkImages as RunnerImages,
)
from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.github_api import GitHubCodeHost, GitHubConfig
from hw_checker.adapters.llm_copilot_sdk import CopilotClientConfig, CopilotSdkLLMClient
from hw_checker.adapters.llm_openai_compatible import LlmClientConfig, OpenAICompatibleLLMClient
from hw_checker.adapters.roster_excel import ExcelRosterReader, RosterColumns
from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.adapters.system_clock import SystemClock
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.errors import ConfigurationError
from hw_checker.config.logging_setup import configure_logging
from hw_checker.config.settings import (
    GitHubSettings,
    LlmSettings,
    RunnerSettings,
    Settings,
    StepLimits,
    load_settings,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.graph import GraphServices, RoundGraph, build_round_graph
from hw_checker.ports import LLMClient
from hw_checker.reporting.writer import MarkdownReportWriter
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.publishing import PublishService
from hw_checker.services.repo_fetch import RepoFetchService
from hw_checker.services.reports import ReportService
from hw_checker.services.roster import RosterService
from hw_checker.services.run_record import RunRecordService

BYTES_PER_KB = 1024
BYTES_PER_MB = 1024 * 1024
CLONES_FOLDER = "clones"
RUNS_FOLDER = "runs"
CHECKPOINTS_FILE = "checkpoints.sqlite"

TRACING_SWITCHES = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")
TRACING_KEYS = ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def build_graph_services(settings: Settings) -> GraphServices:
    """Create every adapter from `settings` and the services over them.

    Brings the database to the latest schema first.

    Raises:
        StorageError: the database cannot be migrated.
        ExecutionEnvironmentError: Docker cannot be reached.
    """
    migrate(settings.storage.database_url)
    storage = SqlStorage(settings.storage.database_url)
    registry = CheckRegistry(builtin_checks())
    workspace = settings.workspace_dir.resolve()
    columns = settings.roster.columns
    git = settings.git
    report_writer = MarkdownReportWriter(workspace / RUNS_FOLDER)
    clock = SystemClock()
    return GraphServices(
        roster=RosterService(
            ExcelRosterReader(
                RosterColumns(
                    name=columns.name,
                    partner_name=columns.partner_name,
                    github_url=columns.github_url,
                ),
                settings.roster.sheet_name,
                settings.roster.header_row,
            )
        ),
        assignments=AssignmentCatalogService(
            YamlAssignmentSource(settings.assignments_dir), registry
        ),
        fetch=RepoFetchService(
            LocalGitClient(
                git.executable,
                git.timeout_seconds,
                git.commit_author_name,
                git.commit_author_email,
                ssl_backend=git.ssl_backend,
                network_retries=git.network_retries,
                retry_backoff_seconds=git.retry_backoff_seconds,
            ),
            workspace / CLONES_FOLDER,
        ),
        execution=ExecutionService(
            DockerCodeRunner(runner_config(settings.runner), _connect_docker(settings.runner)),
            workspace / RUNS_FOLDER,
            settings.runner.logs_root,
        ),
        checks=DeterministicCheckService(DeterministicRequirementChecker(registry)),
        run_records=RunRecordService(storage.runs, clock),
        reports=ReportService(report_writer),
        publishing=PublishService(
            storage.proposals,
            build_code_host(settings),
            build_publishing_git(settings),
            report_writer,
            clock,
            commit_author_configured=bool(git.commit_author_name and git.commit_author_email),
        ),
    )


def build_round_graph_from_settings(
    settings: Settings, checkpointer: Checkpointer = None
) -> RoundGraph:
    """Build the round graph, with one node per assignment in the catalog as it is now.

    Raises:
        InvalidAssignmentError: the assignment data cannot be loaded.
        StorageError: the database cannot be migrated.
        ExecutionEnvironmentError: Docker cannot be reached.
    """
    services = build_graph_services(settings)
    return build_round_graph(
        services.assignments.list_assignments(),
        services,
        settings.max_parallel_repos,
        checkpointer,
    )


@functools.cache
def studio_graph() -> RoundGraph:
    """The `round` graph for LangGraph Studio (`langgraph.json`), built once per server start.

    It has no checkpointer: the dev server supplies its own.

    Raises:
        ConfigurationError: the settings are invalid, or LangSmith tracing is on.
    """
    require_tracing_off()
    settings = load_settings()
    configure_logging(settings)
    return build_round_graph_from_settings(settings)


def require_tracing_off(environ: Mapping[str, str] = os.environ) -> None:
    """Refuse to run while LangSmith tracing could send the round's state off this machine.

    The state holds students' names and findings, so no tracing switch may be on and no
    LangSmith API key may be set.

    Raises:
        ConfigurationError: a tracing switch is on or an API key is set.
    """
    switched_on = [
        name for name in TRACING_SWITCHES if environ.get(name, "").strip().lower() in _TRUE_VALUES
    ]
    keys = [name for name in TRACING_KEYS if environ.get(name, "").strip()]
    if switched_on or keys:
        raise ConfigurationError(
            "LangSmith tracing must stay off: the round's state holds students' names and "
            f"findings. Unset {', '.join(switched_on + keys)} (set LANGSMITH_TRACING=false)."
        )


@contextmanager
def open_checkpointer(settings: Settings) -> Iterator[SqliteSaver]:
    """A SQLite checkpointer in `<workspace>/checkpoints.sqlite`, apart from business data.

    Only the domain's own models and enums may be read back from it.
    """
    path = settings.workspace_dir / CHECKPOINTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(connection, serde=checkpoint_serializer())
    finally:
        connection.close()


def checkpoint_serializer() -> JsonPlusSerializer:
    """A serializer that reads back only safe built-in types and the domain's models."""
    return JsonPlusSerializer(allowed_msgpack_modules=domain_state_types())


def domain_state_types() -> tuple[type, ...]:
    """Every Pydantic model and enum defined in `hw_checker.domain`."""
    package = hw_checker.domain
    found: list[type] = []
    for module_info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(module_info.name)
        found.extend(
            value
            for value in vars(module).values()
            if isinstance(value, type)
            and value.__module__ == module.__name__
            and issubclass(value, (BaseModel, Enum))
        )
    return tuple(found)


@contextmanager
def open_llm_client(settings: Settings) -> Iterator[LLMClient]:
    """The LLM client of the configured provider (`llm.provider`), closed when the block
    ends; for Copilot that stops the Copilot CLI process (ADR 0022).

    Nothing in a round uses it yet, so a missing key or login only matters to its callers.

    Raises:
        ConfigurationError: the provider is OpenRouter and `HW_CHECKER_LLM__API_KEY` is not
            set.
    """
    llm = settings.llm
    if llm.provider == "copilot":
        with CopilotSdkLLMClient(copilot_config(llm), llm.copilot_token) as client:
            yield client
        return
    yield build_openrouter_client(settings)


def build_openrouter_client(settings: Settings) -> OpenAICompatibleLLMClient:
    """The LLM client for the configured OpenAI-compatible endpoint and roles (ADR 0020).

    Raises:
        ConfigurationError: `HW_CHECKER_LLM__API_KEY` is not set.
    """
    api_key = settings.llm.api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise ConfigurationError(
            "The LLM API key is not set. Add HW_CHECKER_LLM__API_KEY=<your key> to .env "
            f"(see .env.example); it is the key for {settings.llm.base_url}."
        )
    return OpenAICompatibleLLMClient(
        llm_config(settings.llm), api_key, http_client=llm_http_client(settings.llm)
    )


def llm_http_client(llm: LlmSettings) -> httpx2.Client:
    """The HTTP client for LLM calls, trusting the system certificate store.

    The SDK's default trusts only certifi's bundle, which rejects a TLS-inspecting
    proxy whose root certificate is installed in the system store.
    """
    return httpx2.Client(verify=ssl.create_default_context(), timeout=llm.timeout_seconds)


def build_code_host(settings: Settings) -> GitHubCodeHost:
    """The GitHub code host for the publishing account; a missing token fails on first use."""
    return GitHubCodeHost(github_config(settings.github), settings.github_token)


def github_config(github: GitHubSettings) -> GitHubConfig:
    """The GitHub code host's configuration from the GitHub settings."""
    return GitHubConfig(
        api_url=github.api_url,
        web_url=github.web_url,
        account=github.account,
        fork_prefix=github.fork_prefix,
        timeout_seconds=github.timeout_seconds,
        fork_wait_seconds=github.fork_wait_seconds,
        fork_poll_seconds=github.fork_poll_seconds,
    )


def build_publishing_git(settings: Settings) -> LocalGitClient:
    """The git client that pushes review branches, with the publishing account's token.

    The round's fetches use a client without the token.
    """
    git = settings.git
    return LocalGitClient(
        git.executable,
        git.timeout_seconds,
        git.commit_author_name,
        git.commit_author_email,
        ssl_backend=git.ssl_backend,
        network_retries=git.network_retries,
        retry_backoff_seconds=git.retry_backoff_seconds,
        push_token=settings.github_token,
    )


def llm_config(llm: LlmSettings) -> LlmClientConfig:
    """The LLM client's configuration from the LLM settings."""
    return LlmClientConfig(
        base_url=llm.base_url,
        models={str(role): model for role, model in llm.roles.items()},
        temperature=llm.temperature,
        max_output_tokens=llm.max_output_tokens,
        timeout_seconds=llm.timeout_seconds,
        max_retries=llm.max_retries,
        retry_backoff_seconds=llm.retry_backoff_seconds,
        max_prompt_chars=llm.max_prompt_chars,
        extra_body=llm.extra_body,
    )


def copilot_config(llm: LlmSettings) -> CopilotClientConfig:
    """The Copilot client's configuration from the LLM settings."""
    return CopilotClientConfig(
        models={str(role): model for role, model in llm.copilot.roles.items()},
        timeout_seconds=llm.timeout_seconds,
        start_timeout_seconds=llm.copilot.start_timeout_seconds,
        max_retries=llm.max_retries,
        retry_backoff_seconds=llm.retry_backoff_seconds,
        max_prompt_chars=llm.max_prompt_chars,
    )


def runner_config(runner: RunnerSettings) -> DockerRunnerConfig:
    """The Docker runner's configuration from the runner settings."""
    return DockerRunnerConfig(
        framework_images={
            framework: RunnerImages(sdk=images.sdk, runtime=images.runtime)
            for framework, images in runner.framework_images.items()
        },
        course_framework=runner.course_framework,
        probe_image=runner.probe_image,
        restore_limits=_limits(runner.restore_limits),
        build_limits=_limits(runner.build_limits),
        run_limits=_limits(runner.run_limits),
        probe_limits=_limits(runner.probe_limits),
        restore_timeout_seconds=runner.restore_timeout_seconds,
        build_timeout_seconds=runner.build_timeout_seconds,
        readiness_timeout_seconds=runner.readiness_timeout_seconds,
        probe_timeout_seconds=runner.probe_timeout_seconds,
        readiness_poll_seconds=runner.readiness_poll_seconds,
        resolve_grace_seconds=runner.resolve_grace_seconds,
        app_port=runner.app_port,
        app_uid=runner.app_uid,
        app_gid=runner.app_gid,
        nuget_cache_volume=runner.nuget_cache_volume,
        label_prefix=runner.label_prefix,
        max_archive_bytes=runner.max_archive_mb * BYTES_PER_MB,
        max_file_bytes=runner.max_file_mb * BYTES_PER_MB,
        max_response_bytes=runner.max_response_kb * BYTES_PER_KB,
    )


def _limits(step: StepLimits) -> ContainerLimits:
    return ContainerLimits(memory_mb=step.memory_mb, cpus=step.cpus, pids=step.pids)


def _connect_docker(runner: RunnerSettings) -> SdkDockerEngine:
    try:
        return SdkDockerEngine.from_environment(timeout_seconds=runner.engine_timeout_seconds)
    except EngineError as error:
        raise ExecutionEnvironmentError(
            f"Cannot connect to Docker ({error}). Start Docker Desktop and try again."
        ) from error
