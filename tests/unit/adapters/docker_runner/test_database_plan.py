"""The database plan: whether a repo uses SQL Server, the names and literals to connect."""

from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.appsettings import connection_names, is_settings_file
from hw_checker.adapters.docker_runner.database_plan import (
    apply_connection,
    plan_database,
    rewrite_connection_literals,
)
from hw_checker.domain import (
    DatabaseLocation,
    DatabaseUnresolvedReason,
    DatabaseUseStatus,
    UnresolvedDatabaseUse,
)

BOM = chr(0xFEFF)
CONNECTION = (  # pragma: allowlist secret
    "Server=sqlserver;Database=hw_run;User Id=hw_run;Password=Aa1_run-secret"
)
STRING = "Server=.\\\\SQLEXPRESS;Database=Shop;Trusted_Connection=True;"
"""A scaffolded connection string as written in a regular C# literal."""


def project(*packages: str) -> str:
    references = "".join(
        f'<PackageReference Include="{name}" Version="9.0.0" />' for name in packages
    )
    return (
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
        f"<TargetFramework>net9.0</TargetFramework></PropertyGroup><ItemGroup>{references}"
        "</ItemGroup></Project>"
    )


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def code(body: str) -> dict[str, str]:
    return {"Shop.csproj": project(), "Data/ShopContext.cs": body}


# ---------------------------------------------------------------------- uses a database


@pytest.mark.parametrize(
    "package",
    [
        "Microsoft.EntityFrameworkCore.SqlServer",
        "microsoft.data.sqlclient",
        "System.Data.SqlClient",
        "Microsoft.EntityFrameworkCore.SqlServer.NetTopologySuite",
    ],
)
def test_a_sql_server_package_means_the_repo_uses_a_database(tmp_path: Path, package: str) -> None:
    plan = plan_database(repo(tmp_path, {"Shop.csproj": project(package)}))

    assert plan.uses_database
    assert plan.status is DatabaseUseStatus.USED


def test_a_use_sql_server_call_means_the_repo_uses_a_database(tmp_path: Path) -> None:
    files = code("class C { void M(B b) { b.UseSqlServer(Configuration.Get()); } }")

    assert plan_database(repo(tmp_path, files)).uses_database


def test_a_connection_string_in_the_code_means_the_repo_uses_a_database(tmp_path: Path) -> None:
    files = code(f'class C {{ string S = "{STRING}"; }}')

    plan = plan_database(repo(tmp_path, files))

    assert plan.uses_database
    assert plan.literals == (DatabaseLocation(file_path="Data/ShopContext.cs", line=1),)


def test_a_use_sql_server_call_in_a_comment_or_a_string_does_not_count(tmp_path: Path) -> None:
    files = code('class C { /* b.UseSqlServer(x) */ string S = "UseSqlServer("; }')

    assert plan_database(repo(tmp_path, files)).status is DatabaseUseStatus.NOT_USED_NO_DATABASE


def test_a_repo_without_a_database_has_none(tmp_path: Path) -> None:
    plan = plan_database(repo(tmp_path, code("class C {}")))

    assert not plan.uses_database
    assert plan.status is DatabaseUseStatus.NOT_USED_NO_DATABASE
    assert plan.use().connection_names == ()


@pytest.mark.parametrize(
    "package",
    [
        "Microsoft.EntityFrameworkCore.Sqlite",
        "Npgsql.EntityFrameworkCore.PostgreSQL",
        "Microsoft.EntityFrameworkCore.InMemory",
        "Pomelo.EntityFrameworkCore.MySql",
        "MySql.Data",
    ],
)
def test_another_provider_alone_means_no_sql_server_database(tmp_path: Path, package: str) -> None:
    plan = plan_database(repo(tmp_path, {"Shop.csproj": project(package)}))

    assert not plan.uses_database
    assert plan.status is DatabaseUseStatus.NOT_USED_OTHER_PROVIDER


@pytest.mark.parametrize(
    "call", ["UseSqlite(x)", "UseNpgsql(x)", "UseInMemoryDatabase(x)", "UseMySql(x)"]
)
def test_another_providers_call_alone_means_no_sql_server_database(
    tmp_path: Path, call: str
) -> None:
    plan = plan_database(repo(tmp_path, code(f"class C {{ void M(B b) {{ b.{call}; }} }}")))

    assert plan.status is DatabaseUseStatus.NOT_USED_OTHER_PROVIDER


def test_sql_server_wins_when_the_repo_also_uses_another_provider(tmp_path: Path) -> None:
    files = {"Shop.csproj": project("Microsoft.EntityFrameworkCore.SqlServer", "Npgsql")}

    assert plan_database(repo(tmp_path, files)).uses_database


def test_build_output_is_not_read(tmp_path: Path) -> None:
    files = {"Shop.csproj": project(), "obj/Shop.csproj": project("System.Data.SqlClient")}

    assert not plan_database(repo(tmp_path, files)).uses_database


# ---------------------------------------------------------------------- names


def used(files: dict[str, str]) -> dict[str, str]:
    """`files` plus a SQL Server package, so the plan is for a repo that uses a database."""
    return {"Shop.csproj": project("Microsoft.EntityFrameworkCore.SqlServer"), **files}


def names_of(tmp_path: Path, body: str) -> tuple[str, ...]:
    files = used({"Program.cs": f"class P {{ void M() {{ {body} }} }}"})
    return plan_database(repo(tmp_path, files)).connection_names


def test_names_come_from_every_settings_file(tmp_path: Path) -> None:
    files = used(
        {
            "appsettings.json": '{"ConnectionStrings": {"Default": "x", "Reports": "y"}}',
            "appsettings.Development.json": '{"ConnectionStrings": {"Local": "z"}}',
            "Api/appsettings.Staging.json": '{"connectionstrings": {"Default": "x"}}',
            "other.json": '{"ConnectionStrings": {"Ignored": "x"}}',
        }
    )

    plan = plan_database(repo(tmp_path, files))

    assert plan.connection_names == ("Default", "Local", "Reports")


def test_settings_with_comments_and_trailing_commas_are_read_as_dotnet_reads_them(
    tmp_path: Path,
) -> None:
    text = (
        BOM
        + """{
      // the database
      "ConnectionStrings": {
        "Default": "Server=a;Database=b;", /* trailing */
        "Url": "http://x//y",
      },
      "Logging": {"Level": ["a", "b",],},
    }"""
    )

    assert connection_names(text) == ["Default", "Url"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("not json", None),
        ("[1, 2]", []),
        ('{"ConnectionStrings": "x"}', []),
        ('{"ConnectionStrings": {"A": {"B": "x"}, "C": ["x"], "D": null}}', ["D"]),
        ('{"Other": {"ConnectionStrings": {"A": "x"}}}', []),
    ],
)
def test_settings_that_are_not_a_connection_section_give_no_names(
    text: str, expected: list[str] | None
) -> None:
    assert connection_names(text) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("appsettings.json", True), ("AppSettings.Development.json", True), ("app.json", False)],
)
def test_which_files_are_settings_files(name: str, expected: bool) -> None:
    assert is_settings_file(name) is expected


def test_a_name_comes_from_get_connection_string(tmp_path: Path) -> None:
    assert names_of(tmp_path, 'var s = config.GetConnectionString("Shop");') == ("Shop",)


def test_names_inside_calls_and_verbatim_literals_are_found(tmp_path: Path) -> None:
    body = (
        'b.UseSqlServer(builder.Configuration.GetConnectionString("Main"));'
        ' var t = c . GetConnectionString ( @"Other" );'
    )

    assert names_of(tmp_path, body) == ("Main", "Other")


def test_names_come_from_the_indexer_forms(tmp_path: Path) -> None:
    body = (
        'var a = config["ConnectionStrings:First"];'
        ' var b = config.GetSection("ConnectionStrings")["Second"];'
        ' var c = config . GetSection ( "connectionstrings" ) [ "Third" ];'
        ' var d = config.GetValue<string>("ConnectionStrings:Fourth");'
    )

    assert names_of(tmp_path, body) == ("First", "Fourth", "Second", "Third")


def test_other_sections_and_calls_give_no_names(tmp_path: Path) -> None:
    body = (
        'var a = config.GetSection("Logging")["Level"]; var b = config["Other:Key"];'
        ' var c = config.GetSection("ConnectionStrings").Value; var d = Get("Key");'
    )

    assert names_of(tmp_path, body) == ()


def test_a_method_named_get_connection_string_is_not_a_call(tmp_path: Path) -> None:
    files = used({"Config.cs": "class C { string GetConnectionString(string name) => name; }"})

    plan = plan_database(repo(tmp_path, files))

    assert plan.connection_names == ()
    assert plan.unresolved == ()


@pytest.mark.parametrize(
    "call",
    [
        "config.GetConnectionString(name)",
        'config.GetConnectionString("A" + suffix)',
        'config.GetConnectionString($"A{x}")',
        'config.GetSection("ConnectionStrings")[key]',
    ],
)
def test_a_name_read_from_a_variable_is_counted_as_not_read(tmp_path: Path, call: str) -> None:
    files = used({"Program.cs": f"class P {{ void M() {{ var s = {call}; }} }}"})

    plan = plan_database(repo(tmp_path, files))

    assert plan.connection_names == ()
    assert plan.unresolved == (
        UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.NAME_NOT_READ, count=1),
    )


def test_a_name_that_cannot_be_an_environment_name_is_left_out_and_counted(
    tmp_path: Path,
) -> None:
    files = used(
        {
            "appsettings.json": '{"ConnectionStrings": {"Good-1.x": "a", "Bad Name": "b"}}',
            "Program.cs": (
                'class P { void M() { c.GetConnectionString("a=b"); c.GetConnectionString(""); } }'
            ),
        }
    )

    plan = plan_database(repo(tmp_path, files))

    assert plan.connection_names == ("Good-1.x",)
    assert plan.unresolved == (
        UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.NAME_NOT_USABLE, count=3),
    )


def test_a_settings_file_that_is_not_json_is_ignored(tmp_path: Path) -> None:
    files = used({"appsettings.json": "{ not json"})

    assert plan_database(repo(tmp_path, files)).connection_names == ()


def test_a_name_in_a_comment_is_not_read(tmp_path: Path) -> None:
    body = '// config.GetConnectionString("Gone")\n/* config["ConnectionStrings:Gone"] */'

    assert names_of(tmp_path, body) == ()


# ---------------------------------------------------------------------- literals


@pytest.mark.parametrize(
    "literal",
    [
        '"Server=.;Database=Shop;Trusted_Connection=True;"',
        '"server=(localdb)\\\\MSSQLLocalDB;initial catalog=Shop;Integrated Security=true"',
        '"Data Source=host,1433; Initial Catalog=Shop;User Id=sa;Password=x;"',
        '@"Server=.\\SQLEXPRESS;Database=Shop;Trusted_Connection=True;"',
        '"""Server=.;Database=Shop;Trusted_Connection=True;"""',
        '"Server=.;Database=\\"Shop\\";Trusted_Connection=True;"',
    ],
)
def test_sql_server_connection_strings_are_found(tmp_path: Path, literal: str) -> None:
    plan = plan_database(repo(tmp_path, code(f"class C {{ string S = {literal}; }}")))

    assert len(plan.literals) == 1


@pytest.mark.parametrize(
    "literal",
    [
        '"Data Source=shop.db"',
        '"Data Source=shop.db;Cache=Shared;"',
        '"Host=localhost;Database=shop;Username=u;Password=p;"',
        '"Server=localhost;Port=5432;Database=shop;User Id=u;Password=p;"',
        '"Server=.;Trusted_Connection=True;"',
        '"Database=Shop;"',
    ],
)
def test_other_strings_are_not_connection_strings(tmp_path: Path, literal: str) -> None:
    plan = plan_database(repo(tmp_path, code(f"class C {{ string S = {literal}; }}")))

    assert plan.literals == ()


def test_a_connection_string_without_a_trailing_semicolon_is_found(tmp_path: Path) -> None:
    plan = plan_database(repo(tmp_path, code('class C { string S = "Server=.;Database=Shop"; }')))

    assert len(plan.literals) == 1


def test_a_connection_string_in_a_comment_is_not_found(tmp_path: Path) -> None:
    body = f'// var s = "{STRING}";\nclass C {{}}'

    assert plan_database(repo(tmp_path, code(body))).literals == ()


def test_each_literal_is_listed_with_its_file_and_line_and_nothing_else(tmp_path: Path) -> None:
    files = used(
        {
            "A.cs": f'class A {{\n  string S = "{STRING}";\n}}',
            "B/B.cs": f'class B {{ string S = "{STRING}"; }}',
        }
    )

    plan = plan_database(repo(tmp_path, files))

    assert plan.literals == (
        DatabaseLocation(file_path="A.cs", line=2),
        DatabaseLocation(file_path="B/B.cs", line=1),
    )
    assert plan.literal_files == ("A.cs", "B/B.cs")
    assert "SQLEXPRESS" not in plan.use().model_dump_json()
    assert "Shop" not in plan.use().model_dump_json()


def test_an_interpolated_connection_string_is_unresolved_and_not_rewritten(
    tmp_path: Path,
) -> None:
    body = 'class C { string S = $"Server={host};Database=Shop;User Id={u};"; }'

    plan = plan_database(repo(tmp_path, used({"A.cs": body})))

    assert plan.literals == ()
    assert plan.unresolved == (
        UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.BUILT_LITERAL, count=1),
    )


def test_a_connection_string_built_by_concatenation_is_unresolved(tmp_path: Path) -> None:
    body = (
        'class C { string S = "Server=" + host + ";Database=Shop;";'
        ' string T = "Server=.;Database=Shop;" + extra; }'
    )

    plan = plan_database(repo(tmp_path, used({"A.cs": body})))

    assert plan.literals == ()
    assert plan.unresolved == (
        UnresolvedDatabaseUse(reason=DatabaseUnresolvedReason.BUILT_LITERAL, count=3),
    )


def test_a_plain_concatenation_of_other_text_is_not_unresolved(tmp_path: Path) -> None:
    files = used({"A.cs": 'class C { string S = "Hello, " + name + "!"; }'})

    assert plan_database(repo(tmp_path, files)).unresolved == ()


def test_the_use_of_a_repo_without_a_database_is_only_its_status(tmp_path: Path) -> None:
    files = {"Shop.csproj": project("Microsoft.EntityFrameworkCore.Sqlite")}
    files["A.cs"] = 'class A { string S = $"Server={x};Database=y;"; }'

    use = plan_database(repo(tmp_path, files)).use()

    assert use.status is DatabaseUseStatus.NOT_USED_OTHER_PROVIDER
    assert use.unresolved == () and use.connection_names == () and use.rewritten_literals == ()


# ---------------------------------------------------------------------- the rewrite


def rewritten(source: str) -> tuple[int, str]:
    return rewrite_connection_literals(source, CONNECTION)


def test_a_regular_literal_is_replaced_and_the_source_stays_valid() -> None:
    source = (
        "class Ctx : DbContext {\n"
        "  protected override void OnConfiguring(DbContextOptionsBuilder o)\n"
        f'    => o.UseSqlServer("{STRING}");\n'
        "}"
    )

    count, new = rewritten(source)

    assert count == 1
    assert new == source.replace(STRING, CONNECTION)
    assert STRING not in new


def test_each_place_a_literal_can_stand_is_replaced() -> None:
    source = (
        f'class C {{ const string Field = "{STRING}"; void M(Db d) {{ d.Open("{STRING}", 3); '
        f'var s = new Opt {{ Connection = "{STRING}" }}; }} }}'
    )

    count, new = rewritten(source)

    assert count == 3
    assert new.count(CONNECTION) == 3
    assert "SQLEXPRESS" not in new


def test_a_verbatim_literal_is_replaced_with_quotes_doubled() -> None:
    source = 'class C { string S = @"Server=.\\SQLEXPRESS;Database=Shop;"; }'

    count, new = rewrite_connection_literals(source, 'Server=a;Database=b;Password=x"y')

    assert count == 1
    assert new == 'class C { string S = @"Server=a;Database=b;Password=x""y"; }'


def test_a_regular_literal_is_replaced_with_backslashes_and_quotes_escaped() -> None:
    source = f'class C {{ string S = "{STRING}"; }}'

    _, new = rewrite_connection_literals(source, 'Server=a;Database=b;Password=x\\y"z')

    assert new == 'class C { string S = "Server=a;Database=b;Password=x\\\\y\\"z"; }'


def test_a_raw_literal_is_replaced_as_written() -> None:
    source = 'class C { string S = """Server=.;Database=Shop;"""; }'

    count, new = rewritten(source)

    assert count == 1
    assert new == f'class C {{ string S = """{CONNECTION}"""; }}'


def test_other_literals_and_built_strings_are_left_as_written() -> None:
    source = (
        'class C { string A = "hello"; string B = $"Server={h};Database=d;";'
        f' string C = "Server=" + h + ";Database=d;"; string D = "{STRING}"; }}'
    )

    count, new = rewritten(source)

    assert count == 1
    assert new == source.replace(STRING, CONNECTION)


def test_a_source_without_a_connection_string_is_unchanged() -> None:
    source = 'class C { string S = "Data Source=shop.db"; }'

    assert rewritten(source) == (0, source)


def test_the_students_files_are_not_touched(tmp_path: Path) -> None:
    files = code(f'class C {{ string S = "{STRING}"; }}')
    root = repo(tmp_path, files)
    plan = plan_database(root)

    result = apply_connection(plan, root, {}, CONNECTION)

    assert (root / "Data/ShopContext.cs").read_text(encoding="utf-8") == files[
        "Data/ShopContext.cs"
    ]
    assert (
        result["Data/ShopContext.cs"].decode("utf-8") == f'class C {{ string S = "{CONNECTION}"; }}'
    )


def test_a_file_a_path_fix_already_changed_is_rewritten_from_its_new_text(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path, code(f'class C {{ string P = "a.txt"; string S = "{STRING}"; }}'))
    fixed = {"Data/ShopContext.cs": f'class C {{ string P = "A.txt"; string S = "{STRING}"; }}'}

    result = apply_connection(
        plan_database(root), root, {k: v.encode() for k, v in fixed.items()}, CONNECTION
    )

    assert result["Data/ShopContext.cs"].decode("utf-8") == (
        f'class C {{ string P = "A.txt"; string S = "{CONNECTION}"; }}'
    )


def test_the_overrides_given_are_not_changed_in_place(tmp_path: Path) -> None:
    root = repo(tmp_path, code(f'class C {{ string S = "{STRING}"; }}'))
    overrides = {"Other.csproj": b"x"}

    result = apply_connection(plan_database(root), root, overrides, CONNECTION)

    assert overrides == {"Other.csproj": b"x"}
    assert set(result) == {"Other.csproj", "Data/ShopContext.cs"}
