"""A project's role, from the data's role patterns (stage 21)."""

import pytest
from pydantic import ValidationError

from hw_checker.checks.deterministic.layers import RolePattern, layers
from hw_checker.domain import (
    ArchitectureEvidence,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    ProjectEvidence,
)

ROLES = {
    "api": RolePattern(sdk="Microsoft.NET.Sdk.Web", name="api|web"),
    "entities": RolePattern(name="entit|model|dto|domain"),
    "services": RolePattern(name=r"servic|business|\bbl\b|logic"),
    "repositories": RolePattern(name=r"repositor|\bdal\b|data|access"),
}
COMPLETE = EvidenceCoverage(status=EvidenceCoverageStatus.COMPLETE, files_examined=1)
WEB = "Microsoft.NET.Sdk.Web"


def evidence(*projects: tuple[str, str]) -> ArchitectureEvidence:
    return ArchitectureEvidence(
        project_files=tuple(path for path, _ in projects),
        projects=tuple(
            ProjectEvidence(file_path=path, sdk_references=(sdk,)) for path, sdk in projects
        ),
        inventory_coverage=COMPLETE,
        signal_coverage=COMPLETE,
    )


def test_each_project_gets_its_role_by_name_ignoring_case() -> None:
    found = layers(
        evidence(
            ("Shop/Shop.csproj", WEB),
            ("Shop.Entities/Shop.Entities.csproj", "Microsoft.NET.Sdk"),
            ("BL/BL.csproj", "Microsoft.NET.Sdk"),
            ("DataAccess/DataAccess.csproj", "Microsoft.NET.Sdk"),
        ),
        ROLES,
    )

    assert dict(found.by_role) == {
        "api": ("Shop/Shop.csproj",),
        "entities": ("Shop.Entities/Shop.Entities.csproj",),
        "services": ("BL/BL.csproj",),
        "repositories": ("DataAccess/DataAccess.csproj",),
    }
    assert found.unassigned == ()
    assert found.missing(("api", "services")) == ()


def test_the_sdk_wins_over_the_name() -> None:
    found = layers(evidence(("Services/Services.csproj", WEB)), ROLES)

    assert found.projects("api") == ("Services/Services.csproj",)
    assert found.projects("services") == ()


@pytest.mark.parametrize(
    "project",
    ["Helpers/Helpers.csproj", "DataModels/DataModels.csproj"],
    ids=["no role", "two roles"],
)
def test_a_project_matching_no_role_or_two_is_unassigned(project: str) -> None:
    found = layers(evidence((project, "Microsoft.NET.Sdk")), ROLES)

    assert found.unassigned == (project,)
    assert found.missing(tuple(ROLES)) == tuple(ROLES)


def test_a_file_belongs_to_the_nearest_project_folder() -> None:
    found = layers(
        evidence(
            ("App.csproj", WEB),
            ("Services/Services.csproj", "Microsoft.NET.Sdk"),
            ("Tools/Tools.csproj", "Microsoft.NET.Sdk"),
        ),
        ROLES,
    )

    assert found.role_of("Controllers/UsersController.cs") == "api"
    assert found.role_of("Services/UserService.cs") == "services"
    assert found.project_of("Tools/Run.cs") == "Tools/Tools.csproj"
    assert found.role_of("Tools/Run.cs") is None


def test_a_role_pattern_needs_an_sdk_or_a_valid_name() -> None:
    with pytest.raises(ValidationError, match="give sdk, name or both"):
        RolePattern()
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        RolePattern(name="(")
