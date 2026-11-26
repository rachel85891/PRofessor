"""What the runner records about a repo's database, and that no secret leaves it."""

import logging
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.domain import (
    DatabaseLocation,
    DatabaseUnresolvedReason,
    DatabaseUse,
    DatabaseUseStatus,
    ExecutionRequest,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    SchemaOutcome,
    UnresolvedDatabaseUse,
)
from hw_checker.domain.errors import ExecutionEnvironmentError
from tests.fakes.execution import FakeDatabaseProvisioner
from tests.unit.adapters.docker_runner.fake_engine import FakeClock, FakeEngine
from tests.unit.adapters.docker_runner.test_engine_and_config import config as base_config
from tests.unit.adapters.docker_runner.test_engine_and_config import sql_server_config
from tests.unit.adapters.docker_runner.test_runner_connection import CONTEXT, ORIGINAL
from tests.unit.adapters.docker_runner.test_runner_database import (
    CONNECTION,
    LOGIN_PASSWORD,
    PASSWORD,
    make_repo,
    runner,
)

SECRETS = (CONNECTION, LOGIN_PASSWORD, PASSWORD, ORIGINAL, "SQLEXPRESS")
PROBE = HttpProbe(
    requirement_id="hw01-r1",
    assignment_id="hw01",
    method=HttpMethod.GET,
    path="/api/products",
    expected_status=200,
)


def execute(
    tmp_path: Path,
    files: dict[str, str],
    engine: FakeEngine | None = None,
    databases: bool = True,
) -> tuple[ExecutionResult, FakeEngine]:
    engine = engine or FakeEngine()
    code_runner = runner(engine, databases=databases)
    code_runner.start_round()
    result = code_runner.execute(
        ExecutionRequest(
            run_id="run-1",
            repo_id="dana-api",
            working_copy=make_repo(tmp_path / "work", files),
            logs_dir=tmp_path / "logs",
            probes=(PROBE,),
        )
    )
    return result, engine


def saved_logs(tmp_path: Path) -> str:
    paths = sorted((tmp_path / "logs").glob("*"))
    return "".join(path.read_text(encoding="utf-8") for path in paths)


def leaks(text: str) -> bool:
    return any(secret in text for secret in SECRETS)


# ---------------------------------------------------------------------- the record


def test_databases_off_records_off_and_changes_nothing_else(tmp_path: Path) -> None:
    result, engine = execute(tmp_path, {"Context.cs": CONTEXT}, databases=False)

    assert result.database_use == DatabaseUse(status=DatabaseUseStatus.OFF)
    assert "database" not in (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")
    assert engine.sql_commands == []
    (app,) = engine.specs("app")
    assert not any(key.startswith("ConnectionStrings") for key in app.environment)


def test_a_repo_with_no_database_records_why(tmp_path: Path) -> None:
    result, _ = execute(tmp_path, {})

    assert result.database_use == DatabaseUse(status=DatabaseUseStatus.NOT_USED_NO_DATABASE)


def test_a_repo_with_another_provider_records_why(tmp_path: Path) -> None:
    files = {"Context.cs": "class C { void M(B b) { b.UseNpgsql(x); } }"}

    result, _ = execute(tmp_path, files)

    assert result.database_use == DatabaseUse(status=DatabaseUseStatus.NOT_USED_OTHER_PROVIDER)


def test_a_repo_that_uses_a_database_records_names_places_and_what_was_left(
    tmp_path: Path,
) -> None:
    files = {
        "Context.cs": CONTEXT,
        "Program.cs": (
            'class P { void M(C c) { var a = c.GetConnectionString("Main");'
            ' var b = c.GetConnectionString(name); var s = $"Server={h};Database=d;"; } }'
        ),
        "appsettings.json": '{"ConnectionStrings": {"Reports": "x"}}',
    }

    result, _ = execute(tmp_path, files)

    assert result.database_use == DatabaseUse(
        status=DatabaseUseStatus.USED,
        connection_names=("Main", "Reports"),
        rewritten_literals=(DatabaseLocation(file_path="Context.cs", line=3),),
        unresolved=(
            UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.BUILT_LITERAL, count=1),
            UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.NAME_NOT_READ, count=1),
        ),
        schema_outcome=SchemaOutcome.NO_CONTEXT,
    )


def test_the_record_is_stored_with_the_result_and_comes_back_equal(tmp_path: Path) -> None:
    result, _ = execute(tmp_path, {"Context.cs": CONTEXT})

    stored = result.model_dump(mode="json")

    assert ExecutionResult.model_validate(stored) == result
    assert stored["database_use"]["status"] == "used"


def test_a_result_stored_before_the_record_existed_still_loads() -> None:
    old = {"build_status": "succeeded", "run_status": "succeeded"}

    assert ExecutionResult.model_validate(old).database_use is None


def test_the_prepare_log_says_what_happened_without_any_text_of_the_literal(
    tmp_path: Path,
) -> None:
    execute(tmp_path, {"Context.cs": CONTEXT})

    log = (tmp_path / "logs" / "prepare.log").read_text(encoding="utf-8")

    assert "database: used" in log
    assert "connection string replaced at Context.cs:3" in log
    assert not leaks(log)


# ---------------------------------------------------------------------- no secret leaves


def test_the_connection_is_redacted_from_every_saved_log_and_the_excerpt(
    tmp_path: Path,
) -> None:
    printed = f"info: connecting with {CONNECTION} and then {LOGIN_PASSWORD}\n"
    engine = FakeEngine(logs_by_kind={"app": printed, "restore": printed, "build": printed})

    result, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    logs = saved_logs(tmp_path)
    assert "connecting with ***" in logs
    assert "connecting with ***" in result.logs_excerpt
    assert not leaks(logs) and not leaks(result.logs_excerpt)
    assert not leaks(result.model_dump_json())


def test_a_build_failure_log_is_redacted_too(tmp_path: Path) -> None:
    engine = FakeEngine(
        exit_codes={"restore": 0, "build": 1},
        logs_by_kind={"build": f"error CS1002 near {CONNECTION}"},
    )

    result, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert "***" in result.logs_excerpt
    assert not leaks(result.model_dump_json())
    assert not leaks(saved_logs(tmp_path))


def test_an_answer_that_echoes_the_connection_is_redacted(tmp_path: Path) -> None:
    engine = FakeEngine(
        probe_responses={("GET", "/api/products"): (500, {}, f"failed: {CONNECTION}")}
    )

    result, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    (probe,) = result.probe_results
    assert probe.response_excerpt == "failed: ***"


def test_nothing_logged_holds_the_connection_the_password_or_the_students_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine = FakeEngine(logs_by_kind={"app": f"{CONNECTION}\n"})

    with caplog.at_level(logging.DEBUG):
        result, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    logged = "\n".join(
        f"{record.getMessage()} {sorted(record.__dict__.values(), key=str)}"
        for record in caplog.records
    )
    assert not leaks(logged)
    assert not leaks(result.model_dump_json())


# ---------------------------------------------------------------------- with a fake provisioner


def runner_with(fake: FakeDatabaseProvisioner, engine: FakeEngine) -> DockerCodeRunner:
    clock = FakeClock()
    config = base_config(
        max_archive_bytes=10_000_000, max_file_bytes=1_000_000, database=sql_server_config()
    )
    return DockerCodeRunner(config, engine, clock=clock, sleep=clock.sleep, database=fake)


def request_for(tmp_path: Path, files: dict[str, str]) -> ExecutionRequest:
    return ExecutionRequest(
        run_id="run-1",
        repo_id="dana-api",
        working_copy=make_repo(tmp_path / "work", files),
        logs_dir=tmp_path / "logs",
    )


def test_the_database_is_created_once_for_a_repo_that_uses_one(tmp_path: Path) -> None:
    fake = FakeDatabaseProvisioner()
    engine = FakeEngine()

    runner_with(fake, engine).execute(request_for(tmp_path, {"Context.cs": CONTEXT}))

    assert [name for name, _ in fake.created] == ["hw_dana_api_run_1"]
    assert fake.dropped == fake.created
    assert fake.calls.index("create_database") < fake.calls.index("drop_database")
    assert engine.leftovers == 0


def test_a_repo_that_uses_none_never_creates_or_drops_a_database(tmp_path: Path) -> None:
    fake = FakeDatabaseProvisioner()

    runner_with(fake, FakeEngine()).execute(request_for(tmp_path, {}))

    assert fake.created == [] and fake.dropped == []


def test_a_refused_create_is_an_environment_error_that_cleans_everything(
    tmp_path: Path,
) -> None:
    fake = FakeDatabaseProvisioner(create_error=ExecutionEnvironmentError("the server refused"))
    engine = FakeEngine()

    with pytest.raises(ExecutionEnvironmentError, match="the server refused"):
        runner_with(fake, engine).execute(request_for(tmp_path, {"Context.cs": CONTEXT}))

    assert fake.dropped == fake.created  # undone even though the create failed
    assert engine.leftovers == 0
    assert engine.specs("app") == []


def test_the_provisioners_redaction_is_applied_to_what_is_saved(tmp_path: Path) -> None:
    fake = FakeDatabaseProvisioner()
    engine = FakeEngine(logs_by_kind={"app": "token fake-secret printed"})

    result = runner_with(fake, engine).execute(request_for(tmp_path, {"Context.cs": CONTEXT}))

    assert "token *** printed" in result.logs_excerpt
    assert "fake-secret" not in saved_logs(tmp_path)
    assert "fake-secret" not in result.model_dump_json()
