from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.checks.deterministic.repository import (
    GitignoreCoversParams,
    NoAbsolutePathsParams,
    NotCommittedParams,
    gitignore_covers,
    masked_path,
    matched_unit,
    no_absolute_paths,
    not_committed,
)
from hw_checker.domain import (
    Assignment,
    CheckInput,
    Finding,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
    TrackedFiles,
)

PATTERNS = ("bin/", "obj/", ".vs/", "*.user")
REQUIREMENT = Requirement(
    id="gen-repo",
    title="Repository hygiene",
    severity=Severity.MINOR,
    kind=RequirementKind.CUSTOM,
    check="not_committed",
    params={"patterns": PATTERNS},
)
ASSIGNMENT = Assignment(id="_general", order=0, title="General", requirements=(REQUIREMENT,))
COVERS = GitignoreCoversParams(patterns=PATTERNS, how_to_add="`dotnet new gitignore`")
COMMITTED = NotCommittedParams(patterns=PATTERNS)
SOURCES = NoAbsolutePathsParams(suffixes=(".cs", ".js"))


def check_input(
    repo: Path, paths: tuple[str, ...] | None = None, ignored: tuple[str, ...] = ()
) -> CheckInput:
    tracked = None if paths is None else TrackedFiles(paths=paths, ignored=ignored)
    return CheckInput(
        repo_dir=repo, assignment=ASSIGNMENT, requirement=REQUIREMENT, tracked=tracked
    )


def only_finding(result: tuple[Finding, ...] | NotEvaluatedReason) -> Finding:
    assert isinstance(result, tuple)
    (finding,) = result
    return finding


def write(root: Path, relative: str, text: str = "class C {}\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- params and matching


@pytest.mark.parametrize("pattern", ["", " bin/", "a/bin/", "/bin/", "!bin/", "bin\\", "bin//"])
def test_patterns_must_be_gitignore_names(pattern: str) -> None:
    with pytest.raises(ValidationError):
        NotCommittedParams(patterns=(pattern,))


def test_suffixes_must_be_file_suffixes() -> None:
    with pytest.raises(ValidationError):
        NoAbsolutePathsParams(suffixes=("cs",))


@pytest.mark.parametrize(
    ("pattern", "path", "unit"),
    [
        ("bin/", "App/bin/Debug/App.dll", "App/bin/"),
        ("bin/", "App/Bin/App.dll", "App/Bin/"),
        ("bin/", "App/bin", None),
        (".vs/", ".vs/App/v17/.suo", ".vs/"),
        ("*.user", "App/App.csproj.user", "App/App.csproj.user"),
        ("*.user", "App/Program.cs", None),
    ],
)
def test_matched_unit_names_the_folder_or_file(pattern: str, path: str, unit: str | None) -> None:
    assert matched_unit(pattern, path) == unit


# ---------------------------------------------------------------- gitignore_covers


def test_gitignore_at_the_root_covering_what_is_tracked_passes(tmp_path: Path) -> None:
    tracked = (".gitignore", "App/App.csproj", "App/bin/App.dll")

    assert gitignore_covers(check_input(tmp_path, tracked, ("App/bin/App.dll",)), COVERS) == ()


def test_gitignore_in_a_subfolder_next_to_the_project_counts(tmp_path: Path) -> None:
    tracked = ("Api/.gitignore", "Api/Api.csproj", "Api/Program.cs")

    assert gitignore_covers(check_input(tmp_path, tracked), COVERS) == ()


def test_gitignore_in_an_unrelated_subfolder_does_not_count(tmp_path: Path) -> None:
    tracked = ("Api/Api.csproj", "Docs/.gitignore")

    finding = only_finding(gitignore_covers(check_input(tmp_path, tracked), COVERS))

    assert finding.message_en == (
        "The repository has no `.gitignore`, so build and IDE files end up in it. "
        "Add a `.gitignore` (`dotnet new gitignore`)."
    )
    assert finding.severity is Severity.MINOR


def test_tracked_files_no_gitignore_leaves_out_are_listed(tmp_path: Path) -> None:
    tracked: tuple[str, ...] = (".gitignore", "App/App.csproj", "App/App.csproj.user")
    tracked = (*tracked, "App/bin/a.dll", "App/bin/b.dll", "App/obj/c.json")

    finding = only_finding(
        gitignore_covers(check_input(tmp_path, tracked, ("App/bin/a.dll",)), COVERS)
    )

    assert finding.message_en == (
        "These are in the repository and no `.gitignore` leaves them out: "
        "`App/App.csproj.user`, `App/bin/`, `App/obj/`. Total: 3. "
        "Add `*.user`, `bin/`, `obj/` to the `.gitignore`."
    )


def test_a_pattern_nothing_matches_passes(tmp_path: Path) -> None:
    assert gitignore_covers(check_input(tmp_path, (".gitignore", "Program.cs")), COVERS) == ()


def test_without_git_state_covers_is_not_evaluated(tmp_path: Path) -> None:
    assert gitignore_covers(check_input(tmp_path), COVERS) is NotEvaluatedReason.ENVIRONMENT_ISSUE


# ---------------------------------------------------------------- not_committed


def test_nothing_committed_passes(tmp_path: Path) -> None:
    assert not_committed(check_input(tmp_path, (".gitignore", "App/Program.cs")), COMMITTED) == ()


def test_tracked_bin_listed_in_gitignore_is_still_a_finding(tmp_path: Path) -> None:
    tracked = (".gitignore", "App/bin/Debug/a.dll", "App/bin/Debug/b.dll")

    finding = only_finding(not_committed(check_input(tmp_path, tracked, tracked[1:]), COMMITTED))

    assert finding.message_en == (
        "These are in the repository but should not be committed: `App/bin/`. Total: 1. "
        "Remove them from the repository (for example `git rm -r --cached App/bin/`); "
        "the `.gitignore` keeps them out after that."
    )


def test_not_committed_lists_five_examples_and_the_total(tmp_path: Path) -> None:
    tracked = tuple(f"P{n}/bin/x.dll" for n in range(7))

    finding = only_finding(not_committed(check_input(tmp_path, tracked), COMMITTED))

    assert "`P4/bin/`." in finding.message_en and "P5" not in finding.message_en
    assert "Total: 7." in finding.message_en


def test_without_git_state_not_committed_is_not_evaluated(tmp_path: Path) -> None:
    assert not_committed(check_input(tmp_path), COMMITTED) is NotEvaluatedReason.ENVIRONMENT_ISSUE


# ---------------------------------------------------------------- no_absolute_paths


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("C:\\\\Users\\\\x\\\\db.txt", "C:\\…"),
        ("D:/data/db.txt", "D:/…"),
        ("/home/someone/app/db.txt", "/home/…"),
        ("/Users/someone/db.txt", "/Users/…"),
        ("users.txt", None),
        ("/api/users/5", None),
        ("/Users/5", None),
        ("Data Source=C:\\\\x.db", None),
        ("C:", None),
    ],
)
def test_masked_path_recognises_full_disk_paths(text: str, masked: str | None) -> None:
    assert masked_path(text) == masked


def test_relative_paths_pass(tmp_path: Path) -> None:
    write(tmp_path, "Api/Services/UserService.cs", 'var path = "users.txt";\n')

    assert no_absolute_paths(check_input(tmp_path), SOURCES) == ()


def test_full_path_gives_a_masked_finding_with_file_and_line(tmp_path: Path) -> None:
    code = 'class S\n{\n    string path = @"D:\\Projects\\Api\\DB.txt";\n}\n'
    write(tmp_path, "Api/Services/UserService.cs", code)
    write(tmp_path, "Api/wwwroot/app.js", 'const f = "C:/data/x.json";\n')

    (finding,) = no_absolute_paths(check_input(tmp_path), SOURCES)

    assert finding.message_en == (
        "The code uses a full path on a disk of your computer, so the project cannot run on "
        "another computer: `D:\\…` (Api/Services/UserService.cs, line 3); "
        "`C:/…` (Api/wwwroot/app.js, line 1). Total: 2. Use a path relative to the project."
    )
    assert finding.evidence is not None
    assert finding.evidence.quote == 'string path = @"D:\\…";'
    assert "Projects" not in finding.evidence.quote
    assert finding.severity is Severity.MINOR


def test_paths_in_comments_and_inside_longer_texts_are_not_reported(tmp_path: Path) -> None:
    code = '// was "C:\\\\old\\\\db.txt"\nvar c = "Server=x;AttachDbFilename=C:\\\\x.mdf";\n'
    write(tmp_path, "Api/Program.cs", code)
    write(tmp_path, "Api/notes.txt", '"C:\\\\x.txt"\n')

    assert no_absolute_paths(check_input(tmp_path), SOURCES) == ()


def test_only_tracked_files_are_read(tmp_path: Path) -> None:
    write(tmp_path, "Api/Program.cs", "var a = 1;\n")
    write(tmp_path, "Api/Generated.cs", 'var p = "C:\\\\x\\\\y.txt";\n')
    write(tmp_path, "Api/bin/Debug/x.cs", 'var p = "C:\\\\x\\\\y.txt";\n')

    tracked = check_input(tmp_path, ("Api/Program.cs", "Api/bin/Debug/x.cs"))
    assert no_absolute_paths(tracked, SOURCES) == ()
    (finding,) = no_absolute_paths(check_input(tmp_path), SOURCES)
    assert "Api/Generated.cs" in finding.message_en and "bin" not in finding.message_en
