"""Composition root: the only place that creates adapters and wires them into services.

Entry points (the CLI, the LangGraph Studio factory) build the round graph from here.
"""

import functools
import importlib
import os
import pkgutil
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from enum import Enum

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
from hw_checker.adapters.roster_excel import ExcelRosterReader, RosterColumns
from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.adapters.system_clock import SystemClock
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.errors import ConfigurationError
from hw_checker.config.logging_setup import configure_logging
from hw_checker.config.settings import RunnerSettings, Settings, StepLimits, load_settings
from hw_checker.domain.errors import ExecutionEnvironmentError
from hw_checker.graph import GraphServices, RoundGraph, build_round_graph
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.repo_fetch import RepoFetchService
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
        run_records=RunRecordService(storage.runs, SystemClock()),
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
