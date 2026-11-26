"""The runner gives the app the run's database connection: by environment and in the code."""

import io
import tarfile
from pathlib import Path

from hw_checker.domain import ExecutionRequest
from tests.unit.adapters.docker_runner.fake_engine import FakeEngine
from tests.unit.adapters.docker_runner.test_runner_database import (
    CONNECTION,
    LOGIN_PASSWORD,
    make_repo,
    runner,
)

STRING = "Server=.\\\\SQLEXPRESS;Database=Shop;Trusted_Connection=True;"
"""The student's own connection string, as written in a regular C# literal."""
ORIGINAL = r"Server=.\SQLEXPRESS;Database=Shop;Trusted_Connection=True;"
CONTEXT = (
    "class Context {\n"
    "  protected override void OnConfiguring(DbContextOptionsBuilder o)\n"
    f'    => o.UseSqlServer("{STRING}");\n'
    "}\n"
)


def run(
    tmp_path: Path, files: dict[str, str], environment: tuple[tuple[str, str], ...] = ()
) -> FakeEngine:
    engine = FakeEngine()
    code_runner = runner(engine)
    code_runner.start_round()
    working_copy = make_repo(tmp_path / "work", files)
    code_runner.execute(
        ExecutionRequest(
            run_id="run-1",
            repo_id="dana-api",
            working_copy=working_copy,
            logs_dir=tmp_path / "logs",
            environment=environment,
        )
    )
    return engine


def archived(engine: FakeEngine, file_name: str) -> list[str]:
    """The text of every archived file called `file_name`, one per archive that has it."""
    texts: list[str] = []
    for data in engine.archive_data:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive.getmembers():
                if member.name.endswith("/" + file_name):
                    texts.append((archive.extractfile(member) or io.BytesIO()).read().decode())
    return texts


def app_environment(engine: FakeEngine) -> dict[str, str]:
    (app,) = engine.specs("app")
    return dict(app.environment)


# ---------------------------------------------------------------------- the code route


def test_a_hard_coded_literal_is_replaced_in_the_run_copy_of_both_volumes(
    tmp_path: Path,
) -> None:
    engine = run(tmp_path, {"Context.cs": CONTEXT})

    texts = archived(engine, "Context.cs")

    assert len(texts) == 2  # the work volume and the data volume
    assert all(CONTEXT.replace(STRING, CONNECTION) == text for text in texts)
    assert all(ORIGINAL not in text and STRING not in text for text in texts)


def test_the_students_working_copy_is_never_written(tmp_path: Path) -> None:
    run(tmp_path, {"Context.cs": CONTEXT})

    assert (tmp_path / "work" / "repo" / "Context.cs").read_text(encoding="utf-8") == CONTEXT


def test_a_verbatim_literal_is_replaced_too(tmp_path: Path) -> None:
    source = r'class C { void M(B b) { b.UseSqlServer(@"Server=.\SQLEXPRESS;Database=Shop;"); } }'

    engine = run(tmp_path, {"Context.cs": source})

    assert archived(engine, "Context.cs")[0] == (
        f'class C {{ void M(B b) {{ b.UseSqlServer(@"{CONNECTION}"); }} }}'
    )


def test_a_literal_in_a_file_with_a_path_fix_gets_both_changes(tmp_path: Path) -> None:
    source = (
        f'class C {{ string P = "Data\\\\users.txt"; string S = "{STRING}"; '
        "void M(B b) { b.UseSqlServer(S); } }"
    )

    engine = run(tmp_path, {"Context.cs": source, "Data/users.txt": ""})

    text = archived(engine, "Context.cs")[0]
    assert '"Data/users.txt"' in text
    assert CONNECTION in text and STRING not in text


# ---------------------------------------------------------------------- the environment route


def test_every_name_the_code_reads_is_given_by_environment(tmp_path: Path) -> None:
    source = (
        "class P { void M(B b) { b.UseSqlServer("
        'config.GetConnectionString("Main")); var s = config["ConnectionStrings:Reports"]; } }'
    )

    engine = run(tmp_path, {"Program.cs": source})

    environment = app_environment(engine)
    assert environment["ConnectionStrings__Main"] == CONNECTION
    assert environment["ConnectionStrings__Reports"] == CONNECTION


def test_every_name_in_the_settings_files_is_given_by_environment(tmp_path: Path) -> None:
    files = {
        "Context.cs": "class C { void M(B b) { b.UseSqlServer(x); } }",
        "appsettings.json": '{"ConnectionStrings": {"Default": "Server=.;Database=A;"}}',
        "appsettings.Development.json": '{"ConnectionStrings": {"Local": "x",},} // a comment',
    }

    engine = run(tmp_path, files)

    environment = app_environment(engine)
    assert environment["ConnectionStrings__Default"] == CONNECTION
    assert environment["ConnectionStrings__Local"] == CONNECTION


def test_the_students_own_settings_files_are_not_changed_in_the_copy(tmp_path: Path) -> None:
    text = '{"ConnectionStrings": {"Default": "Server=.;Database=A;"}}'
    files = {"Context.cs": CONTEXT, "appsettings.json": text}

    engine = run(tmp_path, files)

    assert archived(engine, "appsettings.json") == [text, text]


def test_the_students_environment_cannot_replace_the_connection(tmp_path: Path) -> None:
    files = {
        "Program.cs": 'class P { void M(B b) { b.UseSqlServer(c.GetConnectionString("Main")); } }'
    }
    environment = (("ConnectionStrings__Main", "Server=evil"), ("Other", "kept"))

    engine = run(tmp_path, files, environment)

    app = app_environment(engine)
    assert app["ConnectionStrings__Main"] == CONNECTION
    assert app["Other"] == "kept"
    assert app["ASPNETCORE_HTTP_PORTS"]


def test_a_repo_with_a_literal_and_no_names_gets_no_connection_variable(tmp_path: Path) -> None:
    engine = run(tmp_path, {"Context.cs": CONTEXT})

    assert not any(key.startswith("ConnectionStrings") for key in app_environment(engine))


def test_a_repo_with_no_database_gets_no_connection_at_all(tmp_path: Path) -> None:
    source = 'class P { string S = "Server=.;Database=A;"; }'
    engine = run(tmp_path, {"Program.cs": source.replace("Server=.;Database=A;", "x")})

    assert not any(key.startswith("ConnectionStrings") for key in app_environment(engine))
    assert archived(engine, "Program.cs")[0] == source.replace("Server=.;Database=A;", "x")


def test_the_login_password_is_in_no_command_the_runner_gave_the_engine(tmp_path: Path) -> None:
    engine = run(tmp_path, {"Context.cs": CONTEXT})

    for command in [*engine.sql_commands, *engine.exec_commands]:
        assert LOGIN_PASSWORD not in " ".join(command)
    (app,) = engine.specs("app")
    assert LOGIN_PASSWORD not in " ".join(app.command)
