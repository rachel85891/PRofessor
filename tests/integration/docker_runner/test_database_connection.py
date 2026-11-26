"""The app is given the run's database by every route, against a real SQL Server.

Pulls and starts the pinned SQL Server image (about 2 GB of memory, 15-40 s to start). The
two sample apps need `Microsoft.Data.SqlClient` 6.1.7 in the runner's package cache.
Each sample answers `GET /api/dbname` with `SELECT DB_NAME()`, so the answer is the name of
the database the app is really connected to.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.docker_runner.sql_server import database_name
from hw_checker.domain import (
    DatabaseUseStatus,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    StepStatus,
)
from tests.integration.docker_runner.conftest import (
    FIXTURES,
    OLDER_FRAMEWORK_FIXTURES,
    leftovers,
    make_request,
    probe,
)

pytestmark = pytest.mark.integration

ENV_FIXTURE = FIXTURES / "sample_webapi_db_env"
LITERAL_FIXTURE = FIXTURES / "sample_webapi_db_literal"
NO_DATABASE_FIXTURE = OLDER_FRAMEWORK_FIXTURES["net9.0"]
DB_NAME_PROBES = (probe("hw05-r1", HttpMethod.GET, "/api/dbname", 200),)
ITEMS_PROBES = (probe("hw05-r1", HttpMethod.GET, "/api/items", 200),)
RUN_ID = "it-run"
MAX_DATABASE_NAME_LENGTH = 128
SampleCopy = Callable[..., Path]


def run(
    code_runner: DockerCodeRunner,
    working_copy: Path,
    logs_dir: Path,
    repo_id: str,
    probes: tuple[HttpProbe, ...],
) -> ExecutionResult:
    return code_runner.execute(make_request(working_copy, logs_dir, repo_id=repo_id, probes=probes))


def expected_database(repo_id: str) -> str:
    return database_name(repo_id, RUN_ID, MAX_DATABASE_NAME_LENGTH)


def nothing_secret_was_kept(result: ExecutionResult, logs_dir: Path) -> bool:
    saved = "".join(path.read_text(encoding="utf-8") for path in logs_dir.glob("*"))
    return "Password=" not in saved + result.model_dump_json()


def test_an_app_that_reads_its_connection_by_name_is_connected_to_the_runs_database(
    database_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=ENV_FIXTURE)

    result = run(database_runner, repo, tmp_path / "logs", "it__env", DB_NAME_PROBES)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    (answer,) = result.probe_results
    assert answer.passed and answer.response_excerpt == expected_database("it__env")
    assert result.database_use is not None
    assert result.database_use.status is DatabaseUseStatus.USED
    assert result.database_use.connection_names == ("Main",)
    assert result.database_use.rewritten_literals == ()
    assert nothing_secret_was_kept(result, tmp_path / "logs")


def test_an_app_with_a_hard_coded_connection_string_is_connected_to_the_runs_database(
    database_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=LITERAL_FIXTURE)

    result = run(database_runner, repo, tmp_path / "logs", "it__literal", DB_NAME_PROBES)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    (answer,) = result.probe_results
    assert answer.passed and answer.response_excerpt == expected_database("it__literal")
    assert result.database_use is not None
    assert result.database_use.connection_names == ()
    assert [(place.file_path, place.line) for place in result.database_use.rewritten_literals] == [
        ("Program.cs", 9)
    ]
    assert "MadeUpShop" in (repo / "Program.cs").read_text(encoding="utf-8")  # the repo is intact
    assert nothing_secret_was_kept(result, tmp_path / "logs")


def test_an_app_without_a_database_is_given_none(
    database_runner: DockerCodeRunner,
    engine: SdkDockerEngine,
    sample_copy: SampleCopy,
    tmp_path: Path,
) -> None:
    repo = sample_copy(source=NO_DATABASE_FIXTURE)

    result = run(database_runner, repo, tmp_path / "logs", "it__plain", ITEMS_PROBES)

    assert result.steps == (StepStatus.SUCCEEDED,) * 3, result.logs_excerpt
    assert result.database_use is not None
    assert result.database_use.status is DatabaseUseStatus.NOT_USED_NO_DATABASE


def test_nothing_is_left_in_docker_once_the_round_is_finished(
    database_runner: DockerCodeRunner, engine: SdkDockerEngine
) -> None:
    assert any(resource.kind == "container" for resource in leftovers(engine))  # the server

    database_runner.finish_round()

    assert leftovers(engine) == []
