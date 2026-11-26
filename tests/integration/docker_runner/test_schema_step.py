"""The schema step against a real SQL Server and the six made-up apps (stage HW05-DB-3).

Each app is built and run in Docker with databases on; the runner adds the hosting-startup
file to its copy, the app creates its tables, and the probes write and read a row. The
apps restore from the runner's real package cache (Entity Framework Core 10.0.15).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.schema_step import NO_MARKER, STARTUP_FILE_NAME
from hw_checker.domain import (
    DatabaseUseStatus,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    SchemaOutcome,
    StepStatus,
)
from tests.integration.docker_runner.conftest import (
    FIXTURES,
    leftovers,
    make_request,
    probe,
)

pytestmark = pytest.mark.integration

SCAFFOLD = FIXTURES / "sample_webapi_db_scaffold"
REGISTERED = FIXTURES / "sample_webapi_db_registered"
SPLIT = FIXTURES / "sample_webapi_db_split"
MIGRATIONS = FIXTURES / "sample_webapi_db_migrations"
NO_CONTEXT = FIXTURES / "sample_webapi_db_nocontext"
BROKEN = FIXTURES / "sample_webapi_db_broken"
ITEM_BODY = '{"name":"pen"}'
WRITE_AND_READ = (
    probe("hw05-r1", HttpMethod.POST, "/api/items", 200, ITEM_BODY),
    probe("hw05-r1", HttpMethod.GET, "/api/items", 200).model_copy(
        update={"expect_values": ("pen",)}
    ),
)
SampleCopy = Callable[..., Path]


def run(
    code_runner: DockerCodeRunner,
    repo: Path,
    logs_dir: Path,
    repo_id: str,
    probes: tuple[HttpProbe, ...],
) -> ExecutionResult:
    return code_runner.execute(make_request(repo, logs_dir, repo_id=repo_id, probes=probes))


def row_round_trips(result: ExecutionResult) -> bool:
    """True when the row written by the first probe was read back by the second."""
    return len(result.probe_results) == 2 and all(
        answer.passed and not answer.missing_values for answer in result.probe_results
    )


def saved_text(result: ExecutionResult, logs_dir: Path) -> str:
    saved = "".join(path.read_text(encoding="utf-8") for path in logs_dir.glob("*"))
    return saved + result.model_dump_json()


@pytest.mark.parametrize(
    ("fixture", "outcome"),
    [
        (SCAFFOLD, SchemaOutcome.CREATED),
        (REGISTERED, SchemaOutcome.CREATED),
        (SPLIT, SchemaOutcome.CREATED),
        (MIGRATIONS, SchemaOutcome.MIGRATED),
    ],
    ids=["scaffold", "registered", "split-projects", "migrations"],
)
def test_the_tables_exist_before_the_probes_and_a_row_round_trips(
    database_runner: DockerCodeRunner,
    sample_copy: SampleCopy,
    tmp_path: Path,
    fixture: Path,
    outcome: SchemaOutcome,
) -> None:
    repo = sample_copy(source=fixture)

    result = run(
        database_runner, repo, tmp_path / "logs", f"it__{fixture.name[-9:]}", WRITE_AND_READ
    )

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert row_round_trips(result), [answer.error for answer in result.probe_results]
    assert result.database_use is not None
    assert result.database_use.status is DatabaseUseStatus.USED
    assert result.database_use.schema_outcome is outcome
    assert result.database_use.schema_failure is None
    assert "hw-checker-schema" not in saved_text(result, tmp_path / "logs")
    assert "Password=" not in saved_text(result, tmp_path / "logs")


def test_the_repo_is_left_as_it_was(
    database_runner: DockerCodeRunner, sample_copy: SampleCopy, tmp_path: Path
) -> None:
    repo = sample_copy(source=REGISTERED)
    before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))

    run(database_runner, repo, tmp_path / "logs", "it__untouched", WRITE_AND_READ)

    assert sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*")) == before
    assert not list(repo.rglob(STARTUP_FILE_NAME))


def test_a_repo_with_sql_server_and_no_context_is_unchanged_and_recorded(
    database_runner: DockerCodeRunner, sample_copy: SampleCopy, tmp_path: Path
) -> None:
    repo = sample_copy(source=NO_CONTEXT)
    probes = (probe("hw05-r1", HttpMethod.GET, "/api/dbname", 200),)

    result = run(database_runner, repo, tmp_path / "logs", "it__nocontext", probes)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert result.database_use is not None
    assert result.database_use.status is DatabaseUseStatus.USED
    assert result.database_use.schema_outcome is SchemaOutcome.NO_CONTEXT
    assert result.probe_results[0].passed


def test_a_model_that_cannot_be_created_is_recorded_and_the_build_succeeds(
    database_runner: DockerCodeRunner, sample_copy: SampleCopy, tmp_path: Path
) -> None:
    repo = sample_copy(source=BROKEN)
    probes = (probe("hw05-r1", HttpMethod.GET, "/api/ping", 200),)

    result = run(database_runner, repo, tmp_path / "logs", "it__broken", probes)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert result.database_use is not None
    assert result.database_use.schema_outcome is SchemaOutcome.FAILED
    assert result.database_use.schema_failure == "System.InvalidOperationException"
    assert result.probe_results[0].passed  # the app is up; only its tables are missing


def test_an_app_that_never_answers_is_recorded_as_unknown_and_still_probed(
    database_runner: DockerCodeRunner, sample_copy: SampleCopy, tmp_path: Path
) -> None:
    def keep_the_file_out_of_the_build(repo: Path) -> None:
        project = next(repo.glob("*.csproj"))
        text = project.read_text(encoding="utf-8")
        project.write_text(
            text.replace(
                "<ImplicitUsings>",
                "<EnableDefaultCompileItems>false</EnableDefaultCompileItems><ImplicitUsings>",
            ).replace(
                "</Project>", '<ItemGroup><Compile Include="Program.cs" /></ItemGroup></Project>'
            ),
            encoding="utf-8",
        )

    repo = sample_copy(source=REGISTERED, edit=keep_the_file_out_of_the_build)
    probes = (probe("hw05-r1", HttpMethod.GET, "/api/items", 200),)

    result = run(database_runner, repo, tmp_path / "logs", "it__nomarker", probes)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert result.database_use is not None
    assert result.database_use.schema_outcome is SchemaOutcome.FAILED
    assert result.database_use.schema_failure == NO_MARKER
    assert len(result.probe_results) == 1  # the probes ran anyway


def test_nothing_is_left_in_docker_once_the_round_is_finished(
    database_runner: DockerCodeRunner, engine: SdkDockerEngine
) -> None:
    database_runner.finish_round()

    assert leftovers(engine) == []
