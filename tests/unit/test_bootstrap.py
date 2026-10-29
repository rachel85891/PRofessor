from pathlib import Path

import pytest
from pydantic import BaseModel

from hw_checker import bootstrap
from hw_checker.config.errors import ConfigurationError
from hw_checker.config.settings import REPO_ROOT, RunnerSettings, Settings, StorageSettings
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
