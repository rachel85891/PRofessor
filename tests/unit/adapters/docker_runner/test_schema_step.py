"""The schema step's file, environment and marker line, and the plan and archive parts."""

import io
import re
import tarfile
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.archive import build_repo_archive, build_work_archive
from hw_checker.adapters.docker_runner.database_plan import plan_database
from hw_checker.adapters.docker_runner.schema_startup_source import MARKER_PREFIX, STARTUP_SOURCE
from hw_checker.adapters.docker_runner.schema_step import (
    HOSTING_STARTUP_VARIABLE,
    STARTUP_FILE_NAME,
    SchemaResult,
    parse_schema_marker,
    startup_environment,
    startup_file,
    without_marker,
)
from hw_checker.domain import SchemaOutcome

UID = GID = 1654
USING_DIRECTIVE = re.compile(r"\s*using\s+[\w.]+\s*;")
SQL_CONTEXT = (
    "class ShopDb : DbContext { void M(B b) { b.UseSqlServer(connection); } }\n"
    "class Item { public int Id { get; set; } }\n"
)


def marker(text: str) -> str:
    return f"{MARKER_PREFIX}{text}"


# ---------------------------------------------------------------------- the added file


def test_the_file_is_added_in_the_web_projects_folder() -> None:
    assert list(startup_file("src/Api")) == [f"src/Api/{STARTUP_FILE_NAME}"]
    assert list(startup_file("")) == [STARTUP_FILE_NAME]


def test_the_file_is_the_same_for_every_repo_and_holds_no_text_of_the_students() -> None:
    (first,) = startup_file("A").values()
    (second,) = startup_file("B/C").values()

    assert first == second == STARTUP_SOURCE.encode("utf-8")


def test_the_file_uses_reflection_only_so_it_never_needs_entity_framework() -> None:
    lines = STARTUP_SOURCE.splitlines()

    assert not [line for line in lines if USING_DIRECTIVE.match(line)]
    assert "typeof(Microsoft.EntityFrameworkCore" not in STARTUP_SOURCE
    assert "#nullable disable" in lines and "#pragma warning disable" in lines
    assert "HostingStartup(typeof(HwCheckerSchema.SchemaStartup))" in STARTUP_SOURCE


def test_the_file_prints_the_marker_and_only_an_exception_type_on_failure() -> None:
    assert f'"{MARKER_PREFIX}"' in STARTUP_SOURCE
    assert "__MARKER__" not in STARTUP_SOURCE
    assert '"failed " + Describe(error)' in STARTUP_SOURCE
    assert ".Message" not in STARTUP_SOURCE


def test_the_environment_names_the_web_projects_assembly() -> None:
    assert startup_environment("Shop.Api") == {HOSTING_STARTUP_VARIABLE: "Shop.Api"}


# ---------------------------------------------------------------------- the marker line


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("created", SchemaResult(SchemaOutcome.CREATED)),
        ("migrated", SchemaResult(SchemaOutcome.MIGRATED)),
        ("no_context", SchemaResult(SchemaOutcome.NO_CONTEXT)),
        (
            "failed System.InvalidOperationException",
            SchemaResult(SchemaOutcome.FAILED, "System.InvalidOperationException"),
        ),
        (
            "failed Microsoft.Data.SqlClient.SqlException",
            SchemaResult(SchemaOutcome.FAILED, "Microsoft.Data.SqlClient.SqlException"),
        ),
        ("failed", SchemaResult(SchemaOutcome.FAILED, "unknown")),
        ("failed <script>", SchemaResult(SchemaOutcome.FAILED, "<script>")),
    ],
)
def test_each_outcome_is_read(text: str, expected: SchemaResult) -> None:
    assert parse_schema_marker(marker(text) + "\n") == expected


def test_noise_around_the_line_is_ignored() -> None:
    log = (
        "info: Microsoft.Hosting.Lifetime[14]\r\n"
        f"{marker('migrated')}\r\n"
        "info: Now listening on: http://[::]:8080\n"
    )

    assert parse_schema_marker(log) == SchemaResult(SchemaOutcome.MIGRATED)


def test_the_marker_at_the_very_end_without_a_newline_is_read() -> None:
    assert parse_schema_marker("starting\n" + marker("created")) == SchemaResult(
        SchemaOutcome.CREATED
    )


def test_the_first_marker_counts() -> None:
    log = marker("created") + "\n" + marker("failed System.Exception") + "\n"

    assert parse_schema_marker(log) == SchemaResult(SchemaOutcome.CREATED)


@pytest.mark.parametrize(
    "log",
    [
        "",
        "info: started\n",
        "the line [hw-checker-schema] created is not at the start\n",
        "[hw-checker-schema] invented\n",
        "[hw-checker-schema] created and some words\n",
    ],
)
def test_no_marker_gives_no_answer(log: str) -> None:
    assert parse_schema_marker(log) is None


def test_a_failure_with_words_after_it_is_never_recorded_as_text() -> None:
    log = (
        marker("failed System.Exception Server=x;Password=secret") + "\n"
    )  # pragma: allowlist secret

    assert parse_schema_marker(log) is None


def test_the_marker_is_taken_out_of_the_log() -> None:
    log = f"before\n{marker('created')}\nafter\n"

    assert without_marker(log) == "before\nafter\n"
    assert without_marker("nothing here\n") == "nothing here\n"
    assert without_marker("") == ""


# ---------------------------------------------------------------------- the plan


def write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(text.encode("utf-8"))
    return root


@pytest.mark.parametrize(
    "declaration",
    [
        "class ShopDb : DbContext { }",
        "public partial class ShopContext : Microsoft.EntityFrameworkCore.DbContext { }",
        "class AppDb : IdentityDbContext<User> { }",
        "class AppDb<T>\n    : DbContext\n{ }",
        "class Child : BaseDbContext { }",
    ],
)
def test_a_class_that_derives_from_a_context_is_found(tmp_path: Path, declaration: str) -> None:
    files = {"Db.cs": declaration + "\nclass U { void M(B b) { b.UseSqlServer(c); } }"}

    plan = plan_database(write(tmp_path, files))

    assert plan.uses_database and plan.has_context


@pytest.mark.parametrize(
    "text",
    [
        "class Seeder : ISeeder<ShopDb> { }",
        "class Factory : IDbContextFactory<ShopDb> { }",
        "class Plain { }",
        'class Notes { string s = "class X : DbContext"; }',
        "// class X : DbContext\nclass Plain { }",
    ],
)
def test_anything_else_is_not_a_context(tmp_path: Path, text: str) -> None:
    files = {"Db.cs": text + "\nclass U { void M(B b) { b.UseSqlServer(c); } }"}

    plan = plan_database(write(tmp_path, files))

    assert plan.uses_database and not plan.has_context


def test_a_repo_that_does_not_use_sql_server_has_no_context_in_its_plan(tmp_path: Path) -> None:
    files = {"Db.cs": "class ShopDb : DbContext { void M(B b) { b.UseSqlite(c); } }"}

    plan = plan_database(write(tmp_path, files))

    assert not plan.uses_database and not plan.has_context


# ---------------------------------------------------------------------- the archives


def members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {
            member.name: (archive.extractfile(member) or io.BytesIO()).read()
            for member in archive.getmembers()
        }


def test_an_added_file_is_in_the_work_archive_only_and_the_repo_is_untouched(
    tmp_path: Path,
) -> None:
    repo = write(tmp_path / "repo", {"Api/Api.csproj": "<Project />", "Api/Program.cs": "x"})
    added = {f"Api/{STARTUP_FILE_NAME}": b"// added"}

    work = members(build_work_archive(repo, UID, GID, added=added).data)
    data = members(build_repo_archive(repo, UID, GID).data)

    assert work[f"src/Api/{STARTUP_FILE_NAME}"] == b"// added"
    assert work["src/Api/Program.cs"] == b"x"
    assert f"repo/Api/{STARTUP_FILE_NAME}" not in data
    assert sorted(path.name for path in (repo / "Api").iterdir()) == ["Api.csproj", "Program.cs"]


def test_an_added_file_gets_the_folders_it_needs(tmp_path: Path) -> None:
    repo = write(tmp_path / "repo", {"Api.csproj": "<Project />"})

    work = members(build_work_archive(repo, UID, GID, added={"New/Deep/File.cs": b"y"}).data)

    assert {"src/New", "src/New/Deep"} <= set(work)
    assert work["src/New/Deep/File.cs"] == b"y"


def test_an_added_file_never_replaces_one_of_the_repo(tmp_path: Path) -> None:
    repo = write(tmp_path / "repo", {f"Api/{STARTUP_FILE_NAME}": "the student's own"})

    with pytest.raises(ValueError, match="not a new path"):
        build_work_archive(repo, UID, GID, added={f"Api/{STARTUP_FILE_NAME}": b"// added"})


@pytest.mark.parametrize("path", ["../Escape.cs", "Api/../../Escape.cs", "/Absolute.cs"])
def test_an_added_file_cannot_leave_the_archive(tmp_path: Path, path: str) -> None:
    repo = write(tmp_path / "repo", {"Api.csproj": "<Project />"})

    with pytest.raises(ValueError, match="not a new path"):
        build_work_archive(repo, UID, GID, added={path: b"x"})
