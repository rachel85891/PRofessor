from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.host_paths import (
    fix_source,
    is_absolute_host_path,
    match_project_file,
    plan_path_fixes,
    string_literals,
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
        ('@"C:\\data\\orders.txt"', PathNotRewrittenReason.NO_MATCH),
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
