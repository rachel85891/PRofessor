"""Choosing the Web project when a repo has several (stage 22), one test per step."""

from pathlib import Path

from hw_checker.adapters.docker_runner.discovery import (
    DiscoveryFailure,
    WebProject,
    discover_web_project,
)
from hw_checker.adapters.docker_runner.web_choice import WebChoice, choose_web_project
from hw_checker.domain import WebProjectChoiceReason

WEB = '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup /></Project>'
LIBRARY = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup /></Project>'
PROGRAM = "var builder = WebApplication.CreateBuilder(args);\nbuilder.Build().Run();\n"


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def referencing(*includes: str) -> str:
    items = "".join(f'<ProjectReference Include="{include}" />' for include in includes)
    return f'<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup>{items}</ItemGroup></Project>'


def candidates(repo: Path) -> tuple[WebProject, ...]:
    found = discover_web_project(repo)
    assert isinstance(found, DiscoveryFailure)
    return found.candidates


def choose(repo: Path, recent: tuple[tuple[str, ...], ...] = ()) -> WebChoice | str:
    return choose_web_project(repo, candidates(repo), recent)


def two_apps(repo: Path) -> None:
    """An old single-project API and a new layered one, both with an entry point."""
    write(repo, "OldApi/OldApi.csproj", WEB)
    write(repo, "OldApi/Program.cs", PROGRAM)
    write(repo, "NewApi/NewApi.csproj", WEB)
    write(repo, "NewApi/Program.cs", PROGRAM)


def chosen(outcome: WebChoice | str) -> tuple[str, WebProjectChoiceReason]:
    assert isinstance(outcome, WebChoice), outcome
    return outcome.project.path, outcome.reason


def test_discovery_lists_every_web_project_with_its_resolved_references(tmp_path: Path) -> None:
    write(
        tmp_path, "Api/Api.csproj", referencing("..\\services\\Services.csproj", "../Gone/x.csproj")
    )
    write(tmp_path, "Services/Services.csproj", LIBRARY)
    write(tmp_path, "Old/Old.csproj", WEB)

    found = discover_web_project(tmp_path)

    assert isinstance(found, DiscoveryFailure)
    assert found.message == "several Web projects found: Api/Api.csproj, Old/Old.csproj"
    assert [(c.path, c.references) for c in found.candidates] == [
        ("Api/Api.csproj", ("Services/Services.csproj",)),
        ("Old/Old.csproj", ()),
    ]


def test_a_single_web_project_is_not_a_choice(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", WEB)

    found = discover_web_project(tmp_path)

    assert isinstance(found, WebProject)


def test_step_1_keeps_the_only_project_with_an_entry_point(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", WEB)
    write(tmp_path, "Api/Startup.cs", "public static class P { static void Main(string[] a) {} }")
    write(tmp_path, "Leftover/Leftover.csproj", referencing("../Lib/Lib.csproj"))
    write(
        tmp_path, "Leftover/Controllers/C.cs", "// WebApplication.CreateBuilder(args) in a comment"
    )
    write(tmp_path, "Lib/Lib.csproj", LIBRARY)

    assert chosen(choose(tmp_path)) == ("Api/Api.csproj", WebProjectChoiceReason.ENTRY_POINT)


def test_a_program_file_counts_as_an_entry_point_in_any_case(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", WEB)
    write(tmp_path, "Api/PROGRAM.CS", "")
    write(tmp_path, "Other/Other.csproj", WEB)

    assert chosen(choose(tmp_path)) == ("Api/Api.csproj", WebProjectChoiceReason.ENTRY_POINT)


def test_an_entry_point_of_a_nested_project_is_not_its_parents(tmp_path: Path) -> None:
    write(tmp_path, "Outer.csproj", WEB)
    write(tmp_path, "Inner/Inner.csproj", WEB)
    write(tmp_path, "Inner/Program.cs", PROGRAM)

    assert chosen(choose(tmp_path)) == ("Inner/Inner.csproj", WebProjectChoiceReason.ENTRY_POINT)


def test_step_2_prefers_the_one_that_references_other_projects(tmp_path: Path) -> None:
    two_apps(tmp_path)
    write(tmp_path, "NewApi/NewApi.csproj", referencing("..\\Repository\\Repository.csproj"))
    write(tmp_path, "Repository/Repository.csproj", LIBRARY)

    outcome = choose(tmp_path)

    assert chosen(outcome) == ("NewApi/NewApi.csproj", WebProjectChoiceReason.REFERENCES_PROJECTS)


def test_step_2_without_an_entry_point_anywhere_still_decides(tmp_path: Path) -> None:
    write(tmp_path, "A/A.csproj", WEB)
    write(tmp_path, "B/B.csproj", referencing("../Lib/Lib.csproj"))
    write(tmp_path, "Lib/Lib.csproj", LIBRARY)

    outcome = choose(tmp_path)

    assert chosen(outcome) == ("B/B.csproj", WebProjectChoiceReason.REFERENCES_PROJECTS)


def test_step_3_takes_the_one_the_latest_commits_changed(tmp_path: Path) -> None:
    two_apps(tmp_path)
    recent = (("README.md",), ("newapi/Controllers/UsersController.cs",), ("OldApi/Program.cs",))

    outcome = choose(tmp_path, recent)

    assert chosen(outcome) == ("NewApi/NewApi.csproj", WebProjectChoiceReason.RECENTLY_CHANGED)


def test_step_3_counts_only_the_candidates_left_by_the_earlier_steps(tmp_path: Path) -> None:
    two_apps(tmp_path)
    write(tmp_path, "Third/Third.csproj", WEB)  # no entry point: out at step 1
    recent = (("Third/x.cs",), ("OldApi/Program.cs",))

    outcome = choose(tmp_path, recent)

    assert chosen(outcome) == ("OldApi/OldApi.csproj", WebProjectChoiceReason.RECENTLY_CHANGED)


def test_a_tie_after_every_step_names_the_candidates(tmp_path: Path) -> None:
    two_apps(tmp_path)

    for recent in ((), (("OldApi/Program.cs", "NewApi/Program.cs"),), (("README.md",),)):
        outcome = choose(tmp_path, recent)

        assert isinstance(outcome, str)
        assert outcome.startswith(
            "several Web projects found: NewApi/NewApi.csproj, OldApi/OldApi.csproj; none "
            "could be chosen"
        )
