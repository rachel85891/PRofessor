from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.csharp_source import string_literals
from hw_checker.adapters.docker_runner.host_paths import (
    fix_source,
    is_absolute_host_path,
    match_project_file,
    plan_path_fixes,
)
from hw_checker.domain import PathFixKind, PathNotRewrittenReason, PathRewrite


def write(root: Path, relative: str, content: str | bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return path


# ---------------------------------------------------------------- detection


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Projects\Shop\users.txt",
        r"e:\API\web-api\web-api\wwwroot\Data.txt",
        "D:/data/users.txt",
        r"\\fileserver\share\users.txt",
        r"C:\data",
    ],
)
def test_absolute_host_paths_are_detected(value: str) -> None:
    assert is_absolute_host_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "C:",
        "C:\\",
        "https://example.com/users.txt",
        "http://localhost:5000/api/users",
        "users.txt",
        "./users.txt",
        r"Data\users.txt",
        "/var/data/users.txt",
        "ab:/x.txt",
        r"\\server",
        "{0:C}",
    ],
)
def test_other_strings_are_not_absolute_host_paths(value: str) -> None:
    assert not is_absolute_host_path(value)


def test_literals_of_every_kind_are_decoded() -> None:
    source = (
        'var a = "C:\\\\data\\\\users.txt";\n'
        'var b = @"C:\\data\\users.txt";\n'
        'var c = $"{root}\\\\users.txt";\n'
        'var d = """C:\\data\\raw.txt""";\n'
        'var e = @"say ""hi""";\n'
    )

    literals = string_literals(source)

    assert [(lit.value, lit.line) for lit in literals] == [
        ("C:\\data\\users.txt", 1),
        ("C:\\data\\users.txt", 2),
        ("", 3),
        ("C:\\data\\raw.txt", 4),
        ('say "hi"', 5),
    ]
    assert [lit.verbatim for lit in literals] == [False, True, False, False, True]
    assert literals[2].has_holes and literals[2].interpolated
    assert literals[3].raw


def test_comments_and_character_literals_are_skipped() -> None:
    source = (
        '// var old = "C:\\\\old.txt";\n'
        '/* "C:\\\\block.txt" */\n'
        "var quote = '\"';\n"
        "var slash = '\\\\';\n"
        'var kept = "users.txt";\n'
    )

    assert [lit.value for lit in string_literals(source)] == ["users.txt"]


def test_interpolated_value_is_the_text_before_the_first_hole() -> None:
    (literal,) = string_literals('var p = $"C:\\\\data\\\\{name}.txt";')

    assert literal.value == "C:\\data\\"
    assert literal.has_holes


def test_nested_string_inside_a_hole_does_not_end_the_literal() -> None:
    literals = string_literals('var p = $"x{Get("a\\"b")}y"; var q = "next.txt";')

    assert literals[0].has_holes
    assert literals[-1].value == "next.txt"


# ---------------------------------------------------------------- matching


PROJECT = ["Program.cs", "wwwroot/Data.txt", "users.txt", "a/log.txt", "b/log.txt"]


@pytest.mark.parametrize(
    ("host_path", "expected"),
    [
        (r"E:\API\web-api\web-api\wwwroot\Data.txt", "wwwroot/Data.txt"),
        (r"E:\API\web-api\web-api\WWWROOT\data.TXT", "wwwroot/Data.txt"),
        (r"C:\Users\someone\source\repos\Shop\users.txt", "users.txt"),
        (r"C:\x\Data.txt", "wwwroot/Data.txt"),
        (r"C:\x\a\log.txt", "a/log.txt"),
        (r"C:\x\log.txt", PathNotRewrittenReason.SEVERAL_MATCHES),
        (r"C:\x\c\log.txt", PathNotRewrittenReason.SEVERAL_MATCHES),
        (r"C:\x\orders.txt", PathNotRewrittenReason.NO_MATCH),
        (r"\\server\share\Shop\users.txt", "users.txt"),
    ],
)
def test_longest_trailing_part_must_match_exactly_one_file(
    host_path: str, expected: str | PathNotRewrittenReason
) -> None:
    assert match_project_file(host_path, PROJECT) == expected


# ---------------------------------------------------------------- planning and rewriting


def test_unique_match_is_rewritten_in_the_copy_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = (
        "\ufeffusing System.IO;\r\n"
        "public static class Store\r\n"
        "{\r\n"
        '    const string Path = @"C:\\Projects\\Shop\\WebApi\\users.txt";\r\n'
        '    const string Other = "keep me";\r\n'
        "}\r\n"
    )
    original = write(repo, "WebApi/WebApi/Store.cs", source)
    write(repo, "WebApi/WebApi/users.txt", "")
    write(repo, "WebApi/WebApi/WebApi.csproj", "<Project />")
    before = original.read_bytes()

    plan = plan_path_fixes(repo, "WebApi/WebApi")

    assert plan.rewrites == (
        PathRewrite(
            file_path="WebApi/WebApi/Store.cs",
            line=4,
            kind=PathFixKind.ABSOLUTE,
            original="C:\\Projects\\Shop\\WebApi\\users.txt",
            rewritten="users.txt",
        ),
    )
    assert original.read_bytes() == before, "the working copy is never changed"
    assert plan.overrides == {
        "WebApi/WebApi/Store.cs": before.replace(
            b"C:\\Projects\\Shop\\WebApi\\users.txt", b"users.txt"
        )
    }


def test_rewritten_path_is_relative_to_the_project_folder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "Api/Services/Store.cs", 'var p = "E:\\\\API\\\\web-api\\\\wwwroot\\\\Data.txt";')
    write(repo, "Api/wwwroot/Data.txt", "")

    plan = plan_path_fixes(repo, "Api")

    assert plan.overrides["Api/Services/Store.cs"] == b'var p = "wwwroot/Data.txt";'


@pytest.mark.parametrize(
    ("literal", "reason"),
    [
        ('@"C:\\data\\archive"', PathNotRewrittenReason.NO_MATCH),
        ('@"C:\\data\\log.txt"', PathNotRewrittenReason.SEVERAL_MATCHES),
        ('$"C:\\\\data\\\\{name}.txt"', PathNotRewrittenReason.DYNAMIC),
    ],
)
def test_unsafe_paths_are_recorded_and_left_unchanged(
    tmp_path: Path, literal: str, reason: PathNotRewrittenReason
) -> None:
    repo = tmp_path / "repo"
    write(repo, "Store.cs", f"var p = {literal};")
    write(repo, "a/log.txt", "")
    write(repo, "b/log.txt", "")

    plan = plan_path_fixes(repo, "")

    (rewrite,) = plan.rewrites
    assert (rewrite.rewritten, rewrite.not_rewritten_reason) == (None, reason)
    assert plan.overrides == {}


def test_relative_path_with_other_letter_case_is_rewritten_to_the_real_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    write(repo, "Store.cs", 'File.ReadLines("users.txt"); File.ReadLines("./Data/Seed.txt");')
    write(repo, "Users.txt", "")
    write(repo, "data/seed.txt", "")

    plan = plan_path_fixes(repo, "")

    assert [(r.kind, r.original, r.rewritten) for r in plan.rewrites] == [
        (PathFixKind.CASE, "users.txt", "Users.txt"),
        (PathFixKind.CASE, "./Data/Seed.txt", "data/seed.txt"),
    ]
    assert plan.overrides == {
        "Store.cs": b'File.ReadLines("Users.txt"); File.ReadLines("data/seed.txt");'
    }


def test_relative_path_with_the_right_case_is_not_recorded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "Store.cs", 'File.ReadLines("users.txt"); File.ReadLines("./users.txt");')
    write(repo, "users.txt", "")

    plan = plan_path_fixes(repo, "")

    assert plan.rewrites == () and plan.overrides == {}


def test_files_differing_only_in_case_are_flagged() -> None:
    # Two such files cannot exist on Windows, so the project file list is given directly.
    found, text = fix_source('var p = "USERS.txt";', "Store.cs", ["users.txt", "Users.txt"])

    (rewrite,) = found
    assert rewrite.kind is PathFixKind.CASE
    assert rewrite.not_rewritten_reason is PathNotRewrittenReason.SEVERAL_MATCHES
    assert text == 'var p = "USERS.txt";'


def test_only_the_literal_changes_and_escaping_follows_its_kind() -> None:
    source = (
        'var a = "C:\\\\x\\\\my {file}.txt"; // C:\\x\\my {file}.txt\n'
        'var b = $@"C:\\x\\my {{file}}.txt";\n'
    )

    found, text = fix_source(source, "Store.cs", ["data/my {file}.txt"])

    assert [r.rewritten for r in found] == ["data/my {file}.txt", "data/my {file}.txt"]
    assert text == (
        'var a = "data/my {file}.txt"; // C:\\x\\my {file}.txt\nvar b = $@"data/my {{file}}.txt";\n'
    )


def test_bin_obj_and_non_utf8_files_are_not_scanned(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "bin/Debug/Gen.cs", 'var p = @"C:\\x\\users.txt";')
    write(repo, "obj/Gen.cs", 'var p = @"C:\\x\\users.txt";')
    write(repo, "Legacy.cs", b'var p = @"C:\\x\\\xe9.txt";')

    assert plan_path_fixes(repo, "").rewrites == ()


# ---------------------------------------------------------------- layered repos (stage 22)


def layered(tmp_path: Path, source: str) -> Path:
    """A Web project `Api` and a sibling class library `Repository` holding the data file."""
    repo = tmp_path / "repo"
    write(repo, "Api/Api.csproj", "<Project />")
    write(repo, "Repository/Repository.csproj", "<Project />")
    write(repo, "Repository/Users.txt", "")
    write(repo, "Repository/UserRepository.cs", source)
    return repo


def test_absolute_path_to_a_file_in_a_sibling_project_is_rewritten(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = @"D:\Work\Shop\Repository\users.txt";')

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.kind, rewrite.rewritten) == (PathFixKind.ABSOLUTE, "../Repository/Users.txt")
    assert plan.overrides == {
        "Repository/UserRepository.cs": b'var p = @"../Repository/Users.txt";'
    }


def test_the_longest_matching_tail_picks_the_project_ignoring_case(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = @"D:\Work\Shop\REPOSITORY\users.TXT";')
    write(repo, "Api/Users.txt", "")

    plan = plan_path_fixes(repo, "Api")

    assert [r.rewritten for r in plan.rewrites] == ["../Repository/Users.txt"]


def test_a_file_name_found_in_two_projects_is_not_guessed(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = @"D:\Work\users.txt";')
    write(repo, "Api/Users.txt", "")

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert rewrite.not_rewritten_reason is PathNotRewrittenReason.SEVERAL_MATCHES
    assert plan.overrides == {}


def test_absolute_folder_path_is_rewritten_to_the_repo_folder(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var dir = @"D:\Work\Shop\Repository";')

    plan = plan_path_fixes(repo, "Api")

    assert [r.rewritten for r in plan.rewrites] == ["../Repository"]


@pytest.mark.parametrize(
    ("literal", "kind"),
    [
        (r'"..\\Repository\\Users.txt"', PathFixKind.SEPARATOR),
        (r'@"..\Repository\Users.txt"', PathFixKind.SEPARATOR),
        (r'"../repository/users.txt"', PathFixKind.CASE),
        (r'@"..\repository\users.txt"', PathFixKind.CASE),
    ],
)
def test_relative_path_from_the_web_project_into_a_sibling_is_rewritten(
    tmp_path: Path, literal: str, kind: PathFixKind
) -> None:
    repo = layered(tmp_path, f"var p = {literal};")

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.kind, rewrite.rewritten) == (kind, "../Repository/Users.txt")
    assert plan.overrides["Repository/UserRepository.cs"].endswith(b'"../Repository/Users.txt";')


def test_relative_path_that_works_on_linux_is_left_alone(tmp_path: Path) -> None:
    repo = layered(tmp_path, 'var p = "../Repository/Users.txt"; var q = "../Repository/new.txt";')

    assert plan_path_fixes(repo, "Api").rewrites == ()


def test_relative_backslash_path_to_a_file_the_app_creates_gets_forward_slashes(
    tmp_path: Path,
) -> None:
    repo = layered(tmp_path, r'var p = @"..\Repository\orders.txt";')

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.kind, rewrite.rewritten) == (PathFixKind.SEPARATOR, "../Repository/orders.txt")


def test_relative_path_out_of_the_repo_is_read_by_its_tail(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = @"..\..\Shop\Repository\users.txt";')

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.kind, rewrite.rewritten) == (PathFixKind.RELATIVE, "../Repository/Users.txt")


# ---------------------------------------------------------------- data files not in the repo


@pytest.mark.parametrize(
    ("literal", "created", "rewritten"),
    [
        # The folder the tail names is in the repo: the file is created there.
        (
            r'@"D:\Work\Shop\Repository\orders.txt"',
            "Repository/orders.txt",
            "../Repository/orders.txt",
        ),
        (
            r'@"D:\Work\Shop\REPOSITORY\Data\orders.txt"',
            "Repository/Data/orders.txt",
            "../Repository/Data/orders.txt",
        ),
        # No folder of the repo matches: the Web project's folder.
        (r'@"D:\Work\Elsewhere\orders.txt"', "Api/orders.txt", "orders.txt"),
        (r'@"D:\orders.txt"', "Api/orders.txt", "orders.txt"),
        # A relative path to a folder the repo lacks (git keeps no empty folders).
        (r'"Data/orders.txt"', "Api/Data/orders.txt", "Data/orders.txt"),
        (r'@"..\Shared\orders.txt"', "Shared/orders.txt", "../Shared/orders.txt"),
        # Out of the repo, and its tail names nothing in it.
        (r'@"..\..\x\orders.txt"', "Api/orders.txt", "orders.txt"),
    ],
)
def test_a_missing_data_file_is_created_empty_where_its_path_points(
    tmp_path: Path, literal: str, created: str, rewritten: str
) -> None:
    repo = layered(tmp_path, f"var p = {literal};")
    before = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.rewritten, rewrite.created_path) == (rewritten, created)
    assert rewrite.created_empty and rewrite.not_rewritten_reason is None
    assert plan.created == (created,)
    after = sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*"))
    assert after == before, "nothing is created in the working copy"


def test_an_ambiguous_folder_tail_creates_the_file_in_the_web_project(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = @"D:\Work\Data\orders.txt";')
    write(repo, "Api/Data/seed.txt", "")
    write(repo, "Repository/Data/seed.txt", "")

    plan = plan_path_fixes(repo, "Api")

    assert plan.created == ("Api/orders.txt",)


def test_a_file_named_by_several_literals_is_created_once(tmp_path: Path) -> None:
    repo = layered(
        tmp_path, r'var a = @"D:\x\Repository\orders.txt"; var b = @"E:\y\Repository\orders.txt";'
    )
    write(repo, "Api/Store.cs", r'var c = @"..\Repository\orders.txt";')

    plan = plan_path_fixes(repo, "Api")

    # Api/Store.cs is scanned first; its folder exists, so it only gets `/`.
    assert [r.created_path for r in plan.rewrites] == [None] + ["Repository/orders.txt"] * 2
    assert plan.created == ("Repository/orders.txt",)


@pytest.mark.parametrize(
    "literal",
    [
        r'@"D:\Work\archive"',  # a folder, not a file: nothing to create
        r'$@"D:\Work\{name}.txt"',  # built at run time
    ],
)
def test_nothing_is_created_for_a_path_that_names_no_file(tmp_path: Path, literal: str) -> None:
    repo = layered(tmp_path, f"var p = {literal};")

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert rewrite.rewritten is None and not rewrite.created_empty
    assert plan.created == ()


def test_a_relative_path_whose_folder_exists_creates_nothing(tmp_path: Path) -> None:
    repo = layered(tmp_path, 'var p = "../Repository/orders.txt"; var q = "orders.txt";')

    plan = plan_path_fixes(repo, "Api")

    assert plan.rewrites == () and plan.created == ()


# ---------------------------------------------------------------- windows_path forms (stage 22)


@pytest.mark.parametrize(
    ("literal", "rewritten"),
    [
        # A folder given to Path.Combine, with or without a trailing separator.
        (r'@"..\Repository"', "../Repository"),
        (r'"..\\Repository\\"', "../Repository/"),
        (r'@"..\repository"', "../Repository"),
        # A path appended to the working directory: the leading separator is kept, as `/`.
        (r'"\\..\\Repository\\Users.txt"', "/../Repository/Users.txt"),
        (r'@"\..\Repository\Users.txt"', "/../Repository/Users.txt"),
    ],
)
def test_backslash_folders_and_appended_paths_into_a_sibling_are_rewritten(
    tmp_path: Path, literal: str, rewritten: str
) -> None:
    repo = layered(tmp_path, f"var p = {literal};")

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.kind, rewrite.rewritten) == (PathFixKind.SEPARATOR, rewritten)
    assert plan.overrides["Repository/UserRepository.cs"].endswith(f'"{rewritten}";'.encode())


def test_an_appended_path_to_a_missing_data_file_creates_it(tmp_path: Path) -> None:
    repo = layered(tmp_path, r'var p = Directory.GetCurrentDirectory() + "\\Data\\users.txt";')

    plan = plan_path_fixes(repo, "Api")

    (rewrite,) = plan.rewrites
    assert (rewrite.rewritten, rewrite.created_path) == ("/Data/users.txt", "Api/Data/users.txt")


@pytest.mark.parametrize(
    "literal",
    [
        r'"\\d+"',  # a regex, not a path
        r'"Hello\\World"',  # text with a backslash: no such folder
        r'"\\\\server"',  # the start of a network path
        r'@"..\Missing"',  # a folder that is not in the repo
        r'"\\"',  # a lone separator: built at run time, see the stage report
    ],
)
def test_backslash_text_that_leads_to_no_repo_folder_is_left_alone(
    tmp_path: Path, literal: str
) -> None:
    repo = layered(tmp_path, f"var p = {literal};")

    assert plan_path_fixes(repo, "Api").rewrites == ()


# ---------------------------------------------------------------- project references (stage 22 fix)


def references(tmp_path: Path, *includes: str) -> Path:
    """An Api project referencing `includes`, next to a `Services` class library."""
    repo = tmp_path / "repo"
    items = "\n".join(f'    <ProjectReference Include="{include}" />' for include in includes)
    project = f"<Project>\n  <ItemGroup>\n{items}\n  </ItemGroup>\n</Project>\n"
    write(repo, "Api/Api.csproj", project)
    write(repo, "Services/Services.csproj", "<Project />")
    return repo


def test_a_reference_to_a_folder_in_other_case_is_rewritten_in_the_copy(tmp_path: Path) -> None:
    repo = references(tmp_path, r"..\services\Services.csproj")
    before = (repo / "Api" / "Api.csproj").read_bytes()

    plan = plan_path_fixes(repo, "Api")

    assert plan.rewrites == (
        PathRewrite(
            file_path="Api/Api.csproj",
            line=3,
            kind=PathFixKind.CASE,
            original=r"..\services\Services.csproj",
            rewritten=r"..\Services\Services.csproj",
        ),
    )
    assert plan.overrides["Api/Api.csproj"] == before.replace(rb"\services", rb"\Services")
    assert (repo / "Api" / "Api.csproj").read_bytes() == before


def test_a_reference_to_a_file_name_in_other_case_is_rewritten(tmp_path: Path) -> None:
    repo = references(tmp_path, "../Services/services.CSPROJ")

    plan = plan_path_fixes(repo, "Api")

    assert [(r.kind, r.original, r.rewritten) for r in plan.rewrites] == [
        (PathFixKind.CASE, "../Services/services.CSPROJ", "../Services/Services.csproj")
    ]


def test_an_exact_or_missing_reference_is_left_as_written(tmp_path: Path) -> None:
    repo = references(tmp_path, r"..\Services\Services.csproj", r"..\Entities\Entities.csproj")

    plan = plan_path_fixes(repo, "Api")

    assert plan.rewrites == () and plan.overrides == {}
