from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.discovery import (
    DiscoveryFailure,
    WebProject,
    discover_web_project,
)

WEB = '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>{props}</PropertyGroup></Project>'
LIBRARY = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup /></Project>'


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def web(props: str = "") -> str:
    return WEB.format(props=props)


def test_single_web_project(tmp_path: Path) -> None:
    write(tmp_path, "Shop.Api.csproj", web())

    assert discover_web_project(tmp_path) == WebProject("Shop.Api.csproj", "Shop.Api")


def test_web_project_next_to_a_class_library(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", web())
    write(tmp_path, "Core/Core.csproj", LIBRARY)

    assert discover_web_project(tmp_path) == WebProject("Api/Api.csproj", "Api")


def test_web_project_in_a_nested_folder(tmp_path: Path) -> None:
    write(tmp_path, "src/backend/Shop/Shop.csproj", web())

    result = discover_web_project(tmp_path)

    assert isinstance(result, WebProject)
    assert result.path == "src/backend/Shop/Shop.csproj"


def test_no_project_at_all(tmp_path: Path) -> None:
    write(tmp_path, "Program.cs", "class P {}")

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.message == "no .csproj file found (bin/, obj/ and .git/ are ignored)"


def test_only_a_class_library(tmp_path: Path) -> None:
    write(tmp_path, "Core/Core.csproj", LIBRARY)

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.other_projects == ("Core/Core.csproj",)
    assert "no Web project" in result.message
    assert "Core/Core.csproj" in result.message


def test_two_web_projects_are_named(tmp_path: Path) -> None:
    write(tmp_path, "A/A.csproj", web())
    write(tmp_path, "B/B.csproj", web())

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.web_projects == ("A/A.csproj", "B/B.csproj")
    assert result.message == "several Web projects found: A/A.csproj, B/B.csproj"


def test_projects_under_bin_obj_and_git_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "bin/Debug/Copy.csproj", web())
    write(tmp_path, "obj/Generated.csproj", web())
    write(tmp_path, ".git/Hidden.csproj", web())

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.web_projects == ()
    assert result.message.startswith("no .csproj file found")


def test_bin_copy_does_not_count_as_a_second_web_project(tmp_path: Path) -> None:
    write(tmp_path, "Api.csproj", web())
    write(tmp_path, "bin/Release/Api.csproj", web())

    assert discover_web_project(tmp_path) == WebProject("Api.csproj", "Api")


def test_malformed_xml_is_reported_as_unreadable(tmp_path: Path) -> None:
    write(tmp_path, "Broken.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>')

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.unreadable == ("Broken.csproj",)
    assert "unreadable project files: Broken.csproj" in result.message


def test_malformed_project_next_to_a_valid_web_project_is_named(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", web())
    write(tmp_path, "Old/Old.csproj", "not xml at all")

    result = discover_web_project(tmp_path)

    assert result == WebProject("Api/Api.csproj", "Api", unreadable=("Old/Old.csproj",))


def test_assembly_name_comes_from_the_project_property(tmp_path: Path) -> None:
    write(tmp_path, "Api.csproj", web("<AssemblyName>Shop.WebApi</AssemblyName>"))

    assert discover_web_project(tmp_path) == WebProject("Api.csproj", "Shop.WebApi")


def test_assembly_name_expression_is_not_evaluated(tmp_path: Path) -> None:
    write(tmp_path, "Api.csproj", web("<AssemblyName>$(MSBuildProjectName).Web</AssemblyName>"))

    assert discover_web_project(tmp_path) == WebProject("Api.csproj", "Api")


def test_sdk_with_version_and_sdk_element_are_recognised(tmp_path: Path) -> None:
    write(tmp_path, "A/A.csproj", '<Project Sdk="Microsoft.NET.Sdk.Web/9.0.100"></Project>')
    write(tmp_path, "B/B.csproj", '<Project><Sdk Name="Microsoft.NET.Sdk.Web" /></Project>')

    result = discover_web_project(tmp_path)

    assert isinstance(result, DiscoveryFailure)
    assert result.web_projects == ("A/A.csproj", "B/B.csproj")


def test_external_entities_are_not_resolved(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    write(
        tmp_path,
        "Api.csproj",
        f'<!DOCTYPE p [<!ENTITY x SYSTEM "{secret.as_uri()}">]>'
        '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
        "<AssemblyName>&x;</AssemblyName></PropertyGroup></Project>",
    )

    result = discover_web_project(tmp_path)

    assert not (isinstance(result, WebProject) and "SECRET" in result.assembly_name)


def test_the_committed_sample_fixture_is_found() -> None:
    fixture = Path(__file__).parents[3] / "fixtures" / "sample_webapi"

    assert discover_web_project(fixture) == WebProject(
        "SampleWebApi.csproj", "SampleWebApi", target_framework="net10.0"
    )


def test_target_framework_is_read(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj", web("<TargetFramework> net10.0 </TargetFramework>"))

    result = discover_web_project(tmp_path)

    assert result == WebProject("Api/Api.csproj", "Api", target_framework="net10.0")
    assert isinstance(result, WebProject)
    assert result.framework_problem() is None
    assert result.folder == "Api"


def test_target_frameworks_plural_is_read(tmp_path: Path) -> None:
    write(tmp_path, "Api.csproj", web("<TargetFrameworks>net8.0;net10.0</TargetFrameworks>"))

    result = discover_web_project(tmp_path)

    assert isinstance(result, WebProject)
    assert result.target_framework == "net8.0;net10.0"
    assert result.folder == ""


@pytest.mark.parametrize(
    ("framework", "problem"),
    [
        ("net9.0", "Api.csproj targets net9.0; only net10.0 is supported"),
        ("net10.0-windows", "Api.csproj targets net10.0-windows; only net10.0 is supported"),
        ("NET10.0", None),
        (None, "no <TargetFramework> found in Api.csproj; only net10.0 is supported"),
    ],
)
def test_only_net10_is_supported(framework: str | None, problem: str | None) -> None:
    project = WebProject("Api.csproj", "Api", target_framework=framework)

    assert project.framework_problem() == problem


def test_folder_names_with_bidi_marks_and_hebrew_are_found(tmp_path: Path) -> None:
    folder = "\u200fשיעורי בית\u200f/Web Api"  # noqa: RUF001
    write(tmp_path, f"{folder}/Web Api.csproj", web("<TargetFramework>net10.0</TargetFramework>"))

    result = discover_web_project(tmp_path)

    assert result == WebProject(f"{folder}/Web Api.csproj", "Web Api", target_framework="net10.0")
    assert isinstance(result, WebProject)
    assert result.folder == folder
