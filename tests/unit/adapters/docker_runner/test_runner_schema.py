"""The runner's schema step: the file and environment it adds, the wait, and the record."""

import io
import logging
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.engine import ExecResult
from hw_checker.adapters.docker_runner.schema_startup_source import MARKER_PREFIX
from hw_checker.adapters.docker_runner.schema_step import (
    HOSTING_STARTUP_VARIABLE,
    NO_MARKER,
    STARTUP_FILE_NAME,
)
from hw_checker.domain import (
    DatabaseUse,
    DatabaseUseStatus,
    ExecutionRequest,
    ExecutionResult,
    HttpMethod,
    HttpProbe,
    SchemaOutcome,
)
from tests.unit.adapters.docker_runner.fake_engine import FakeEngine
from tests.unit.adapters.docker_runner.test_runner_database import (
    CONNECTION,
    make_repo,
    only_the_server_is_left,
    runner,
)

CONTEXT = "class ShopDb : DbContext { void M(B b) { b.UseSqlServer(connection); } }"
CONTEXT_WITH_LITERAL = (
    "class ShopDb : DbContext {\n"
    '  void M(B b) { b.UseSqlServer("Server=.;Database=Shop;Trusted_Connection=True;"); }\n'
    "}\n"
)
PROBE = HttpProbe(
    requirement_id="hw05-r1",
    assignment_id="hw05",
    method=HttpMethod.GET,
    path="/api/products",
    expected_status=200,
)
STARTUP_PATH = f"src/{STARTUP_FILE_NAME}"


@dataclass
class SlowSchemaEngine(FakeEngine):
    """The app prints its marker only on its `marker_after`-th log read, as a slow start does."""

    marker_after: int = 3
    marker: str = f"{MARKER_PREFIX}created\n"
    reads: int = 0
    events: list[str] = field(default_factory=list)

    def logs(self, container: str) -> str:
        if self.containers[container].kind != "app":
            return super().logs(container)
        self.reads += 1
        if self.reads < self.marker_after:
            return "starting\n"
        self.events.append("marker")
        return f"starting\n{self.marker}"

    def exec(
        self,
        container: str,
        command: tuple[str, ...],
        environment: Mapping[str, str] | None = None,
    ) -> ExecResult:
        if any(part.endswith("/api/products") for part in command):
            self.events.append("probe")
        return super().exec(container, command, environment)


def execute(
    tmp_path: Path,
    files: dict[str, str],
    engine: FakeEngine | None = None,
    databases: bool = True,
) -> tuple[ExecutionResult, FakeEngine, Path]:
    engine = engine or FakeEngine(logs_by_kind={"app": f"{MARKER_PREFIX}created\n"})
    code_runner = runner(engine, databases=databases)
    code_runner.start_round()
    working_copy = make_repo(tmp_path / "work", files)
    result = code_runner.execute(
        ExecutionRequest(
            run_id="run-1",
            repo_id="dana-api",
            working_copy=working_copy,
            logs_dir=tmp_path / "logs",
            probes=(PROBE,),
        )
    )
    return result, engine, working_copy


def tar_members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {
            member.name: (archive.extractfile(member) or io.BytesIO()).read()
            for member in archive.getmembers()
        }


def work_archive(engine: FakeEngine) -> dict[str, bytes]:
    """The archive of the work volume (the first copied in), where the app is built."""
    return tar_members(engine.archive_data[0])


def saved_logs(tmp_path: Path) -> str:
    return "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*"))


def schema_use(result: ExecutionResult) -> DatabaseUse:
    assert result.database_use is not None
    return result.database_use


# ---------------------------------------------------------------------- when it is added


def test_a_repo_with_a_context_gets_the_file_and_the_environment_in_its_copy_only(
    tmp_path: Path,
) -> None:
    result, engine, working_copy = execute(tmp_path, {"Context.cs": CONTEXT})

    assert STARTUP_PATH in work_archive(engine)
    (app,) = engine.specs("app")
    assert app.environment[HOSTING_STARTUP_VARIABLE] == "Shop.Api"
    assert schema_use(result).schema_outcome is SchemaOutcome.CREATED
    assert not (working_copy / STARTUP_FILE_NAME).exists()  # the repo is untouched


def test_the_file_is_not_in_the_apps_writable_copy_of_the_repo(tmp_path: Path) -> None:
    _, engine, _ = execute(tmp_path, {"Context.cs": CONTEXT})

    assert not any(STARTUP_FILE_NAME in name for name in tar_members(engine.archive_data[1]))


def test_no_file_of_the_students_changes_but_the_connection_literal(tmp_path: Path) -> None:
    files = {"Context.cs": CONTEXT_WITH_LITERAL}

    _, engine, working_copy = execute(tmp_path, files)

    archive = work_archive(engine)
    assert set(archive) - {"src", "out", STARTUP_PATH} == {
        "src/Shop.Api.csproj",
        "src/Controllers",
        "src/Controllers/ProductsController.cs",
        "src/Context.cs",
    }
    assert archive["src/Controllers/ProductsController.cs"] == b"class C {}"
    assert (working_copy / "Context.cs").read_text(encoding="utf-8") == CONTEXT_WITH_LITERAL


def test_databases_off_adds_no_file_and_no_environment(tmp_path: Path) -> None:
    result, engine, _ = execute(tmp_path, {"Context.cs": CONTEXT}, databases=False)

    assert STARTUP_PATH not in work_archive(engine)
    (app,) = engine.specs("app")
    assert HOSTING_STARTUP_VARIABLE not in app.environment
    assert schema_use(result) == DatabaseUse(status=DatabaseUseStatus.OFF)


def test_a_repo_with_no_database_adds_no_file_and_no_environment(tmp_path: Path) -> None:
    result, engine, _ = execute(tmp_path, {"Plain.cs": "class Plain { }"})

    assert STARTUP_PATH not in work_archive(engine)
    (app,) = engine.specs("app")
    assert HOSTING_STARTUP_VARIABLE not in app.environment
    assert schema_use(result).schema_outcome is SchemaOutcome.NOT_ATTEMPTED


def test_a_repo_with_sql_server_and_no_context_is_left_unchanged_and_recorded(
    tmp_path: Path,
) -> None:
    files = {"Data.cs": "class Data { void M(B b) { b.UseSqlServer(connection); } }"}

    result, engine, _ = execute(tmp_path, files)

    assert STARTUP_PATH not in work_archive(engine)
    (app,) = engine.specs("app")
    assert HOSTING_STARTUP_VARIABLE not in app.environment
    assert schema_use(result).status is DatabaseUseStatus.USED
    assert schema_use(result).schema_outcome is SchemaOutcome.NO_CONTEXT


def test_a_file_the_repo_already_has_at_that_path_is_never_replaced(tmp_path: Path) -> None:
    files = {"Context.cs": CONTEXT, STARTUP_FILE_NAME: "the student's own"}

    result, engine, working_copy = execute(tmp_path, files)

    assert work_archive(engine)[STARTUP_PATH] == b"the student's own"
    (app,) = engine.specs("app")
    assert HOSTING_STARTUP_VARIABLE not in app.environment
    assert schema_use(result).schema_outcome is SchemaOutcome.NOT_ATTEMPTED
    assert (working_copy / STARTUP_FILE_NAME).read_text(encoding="utf-8") == "the student's own"


# ---------------------------------------------------------------------- the answer


@pytest.mark.parametrize(
    ("line", "outcome", "failure"),
    [
        ("created", SchemaOutcome.CREATED, None),
        ("migrated", SchemaOutcome.MIGRATED, None),
        ("no_context", SchemaOutcome.NO_CONTEXT, None),
        (
            "failed System.InvalidOperationException",
            SchemaOutcome.FAILED,
            "System.InvalidOperationException",
        ),
    ],
)
def test_each_answer_is_recorded(
    tmp_path: Path, line: str, outcome: SchemaOutcome, failure: str | None
) -> None:
    engine = FakeEngine(logs_by_kind={"app": f"noise\n{MARKER_PREFIX}{line}\nmore noise\n"})

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert schema_use(result).schema_outcome is outcome
    assert schema_use(result).schema_failure == failure


def test_the_marker_line_is_kept_out_of_the_saved_log_and_the_excerpt(tmp_path: Path) -> None:
    engine = FakeEngine(logs_by_kind={"app": f"noise\n{MARKER_PREFIX}created\nmore noise\n"})

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert "hw-checker-schema" not in saved_logs(tmp_path) + result.logs_excerpt
    assert "noise" in saved_logs(tmp_path)


def test_the_probes_run_only_after_the_marker(tmp_path: Path) -> None:
    engine = SlowSchemaEngine(marker_after=4)

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert engine.events.index("marker") < engine.events.index("probe")
    assert len(result.probe_results) == 1
    assert schema_use(result).schema_outcome is SchemaOutcome.CREATED


def test_a_failed_schema_does_not_fail_the_run_and_the_probes_still_run(tmp_path: Path) -> None:
    engine = FakeEngine(
        logs_by_kind={"app": f"{MARKER_PREFIX}failed System.InvalidOperationException\n"}
    )

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert result.failure_kind is None
    assert len(result.probe_results) == 1
    assert schema_use(result).schema_outcome is SchemaOutcome.FAILED


def test_no_marker_before_the_timeout_is_unknown_and_the_probes_still_run(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(logs_by_kind={"app": "starting\n"})

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert schema_use(result).schema_outcome is SchemaOutcome.FAILED
    assert schema_use(result).schema_failure == NO_MARKER
    assert len(result.probe_results) == 1 and result.probe_results[0].passed


def test_the_wait_is_bounded_by_the_configured_timeout(tmp_path: Path) -> None:
    engine = SlowSchemaEngine(marker_after=10_000)

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert engine.reads < 1_000  # 60 s at 0.1 s a poll, not forever
    assert schema_use(result).schema_failure == NO_MARKER


def test_an_app_that_crashed_before_answering_is_unknown_and_nothing_hangs(
    tmp_path: Path,
) -> None:
    engine = FakeEngine(app="crash", logs_by_kind={"app": "Unhandled exception\n"})

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert result.failure_kind is not None
    assert schema_use(result).schema_failure == NO_MARKER


# ---------------------------------------------------------------------- nothing secret or left


def test_a_marker_with_text_after_the_type_never_reaches_the_record(tmp_path: Path) -> None:
    line = f"{MARKER_PREFIX}failed System.Exception {CONNECTION}\n"
    engine = FakeEngine(logs_by_kind={"app": line})

    result, _, _ = execute(tmp_path, {"Context.cs": CONTEXT}, engine)

    assert CONNECTION not in result.model_dump_json() + saved_logs(tmp_path)
    assert schema_use(result).schema_failure == NO_MARKER


def test_nothing_logged_holds_the_marker_or_the_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        execute(tmp_path, {"Context.cs": CONTEXT})

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert CONNECTION not in logged and MARKER_PREFIX not in logged


def test_the_step_creates_no_resource_of_its_own_and_cleanup_leaves_nothing(
    tmp_path: Path,
) -> None:
    _, with_step, _ = execute(tmp_path / "with", {"Context.cs": CONTEXT})
    without = FakeEngine(logs_by_kind={"app": "x\n"})
    data = {"Data.cs": "class Data { void M(B b) { b.UseSqlServer(connection); } }"}
    execute(tmp_path / "without", data, without)

    assert sorted(item.kind for item in with_step.created) == sorted(
        item.kind for item in without.created
    )
    assert only_the_server_is_left(with_step) and only_the_server_is_left(without)
