"""The layer and DI checks (stage 21) on stage 19's four-project solution and its variants:
a pass, a finding and a "can't tell" case for each."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry, CheckResult
from hw_checker.domain import (
    ArchitectureEvidence,
    Assignment,
    CheckInput,
    CheckPassed,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    Finding,
    NotEvaluatedReason,
    ProjectReferenceStatus,
    Requirement,
    RequirementKind,
    Severity,
)
from tests.unit.layered_solution import FILES, variant, write_solution

ROLES: dict[str, Any] = {
    "api": {"sdk": "Microsoft.NET.Sdk.Web", "name": "api|web"},
    "entities": {"name": "entit|model|dto|domain"},
    "services": {"name": r"servic|business|\bbl\b|logic"},
    "repositories": {"name": r"repositor|\bdal\b|data|access"},
}
LAYERS = ["api", "entities", "services", "repositories"]
REFERENCES: dict[str, Any] = {
    "required": [
        ["api", "services"],
        ["services", "repositories"],
        ["services", "entities"],
        ["repositories", "entities"],
    ],
    "forbidden": [
        ["api", "repositories"],
        ["repositories", "services"],
        ["services", "api"],
        ["entities", "*"],
    ],
}
FILE_ACCESS: dict[str, Any] = {
    "patterns": [r"\bFile\.\w+", r"\bStreamReader\b", r"\bStreamWriter\b", r"\bFileStream\b"],
    "allowed": ["repositories"],
}
REGISTRY = CheckRegistry(builtin_checks())
CONTROLLER = "Api/Controllers/UsersController.cs"
SERVICE = "Services/UserService.cs"
PROGRAM = "Api/Program.cs"
LIBRARY = '<Project Sdk="Microsoft.NET.Sdk" />\n'


def run(
    check: str, root: Path, evidence: ArchitectureEvidence | None = None, **params: Any
) -> CheckResult:
    requirement = Requirement(
        id="hw-req",
        title="A layer requirement",
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check=check,
        params={"roles": ROLES, **params},
    )
    registered = REGISTRY.get(check)
    model = registered.spec.params_model.model_validate(dict(requirement.params))
    check_input = CheckInput(
        repo_dir=root,
        assignment=Assignment(id="hw02", order=2, title="Layers", requirements=(requirement,)),
        requirement=requirement,
        architecture=evidence or LocalArchitectureEvidenceReader().scan(root),
    )
    return registered.function(check_input, model)


def solution(tmp_path: Path, files: Mapping[str, str] = FILES) -> Path:
    return write_solution(tmp_path, files)


def message(outcome: CheckResult) -> str:
    assert isinstance(outcome, tuple), outcome
    [finding] = outcome
    assert isinstance(finding, Finding)
    return finding.message_en


def unavailable() -> ArchitectureEvidence:
    coverage = EvidenceCoverage(status=EvidenceCoverageStatus.UNAVAILABLE, files_examined=0)
    return ArchitectureEvidence(inventory_coverage=coverage, signal_coverage=coverage)


def references(*targets: str) -> str:
    items = "".join(f'    <ProjectReference Include="..\\{t}\\{t}.csproj" />\n' for t in targets)
    return f'<Project Sdk="Microsoft.NET.Sdk">\n  <ItemGroup>\n{items}  </ItemGroup>\n</Project>\n'


SINGLE_PROJECT = {
    "App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
    "App/Controllers/UsersController.cs": (
        "public class UsersController : ControllerBase\n"
        "{\n"
        '    public string Get() => System.IO.File.ReadAllText("users.txt");\n'
        "}\n"
    ),
}
CONTROLLER_TAKES_CLASS = (
    FILES[CONTROLLER]
    .replace("IUserService _service", "UserService _service")
    .replace("UsersController(IUserService service)", "UsersController(UserService service)")
)
SERVICE_CREATES = (
    "using Entities;\n"
    "using Repositories;\n"
    "\n"
    "namespace Services;\n"
    "\n"
    "public class UserService : IUserService\n"
    "{\n"
    "    private readonly UserRepository _repository = new UserRepository(new SystemClock());\n"
    "\n"
    "    public Task<User?> GetById(int id)\n"
    "    {\n"
    '        var empty = new User { Name = "" };\n'
    "        return _repository.GetById(id);\n"
    "    }\n"
    "}\n"
)


# ---------------------------------------------------------------- layer_projects


def test_layer_projects_pass_with_the_four_layers(tmp_path: Path) -> None:
    outcome = run("layer_projects", solution(tmp_path), required=LAYERS)

    assert outcome == CheckPassed(
        message="Layer projects: api `Api`, entities `Entities`, services `Services`, "
        "repositories `Repositories`."
    )


def test_layer_projects_name_the_missing_roles_of_a_single_project(tmp_path: Path) -> None:
    outcome = run("layer_projects", solution(tmp_path, SINGLE_PROJECT), required=LAYERS)

    assert message(outcome) == (
        "The code is not split into the layer projects yet: no project was found for "
        "entities, services, repositories. Projects found: `App`."
    )


def test_layer_projects_list_an_unassigned_project(tmp_path: Path) -> None:
    files = variant(
        {"Helpers/Helpers.csproj": LIBRARY, "DataModels/DataModels.csproj": LIBRARY},
        removed=("Services/Services.csproj", "Services/IUserService.cs", SERVICE),
    )

    outcome = run("layer_projects", solution(tmp_path, files), required=LAYERS)

    assert message(outcome) == (
        "The code is not split into the layer projects yet: no project was found for "
        "services. Projects found: `Api`, `DataModels`, `Entities`, `Helpers`, "
        "`Repositories`. With no clear layer by name: `DataModels`, `Helpers`."
    )


def test_layer_projects_with_two_projects(tmp_path: Path) -> None:
    files = {
        "Api/Api.csproj": references("Repositories"),
        "Repositories/Repositories.csproj": LIBRARY,
    }

    outcome = run("layer_projects", solution(tmp_path, files), required=LAYERS)

    assert "no project was found for entities, services." in message(outcome)


def test_layer_projects_cannot_tell_without_the_inventory(tmp_path: Path) -> None:
    outcome = run("layer_projects", tmp_path, unavailable(), required=LAYERS)

    assert outcome is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- layer_references


def test_layer_references_pass_on_the_layered_solution(tmp_path: Path) -> None:
    assert run("layer_references", solution(tmp_path), **REFERENCES) == ()


def test_api_referencing_repositories_skips_a_layer(tmp_path: Path) -> None:
    files = variant({"Api/Api.csproj": references("Services", "Entities", "Repositories")})

    outcome = run("layer_references", solution(tmp_path, files), **REFERENCES)

    assert message(outcome) == (
        "The project references do not follow the layers: `Api` references `Repositories` "
        "(api → repositories is not allowed). Total: 1."
    )


def test_a_reversed_reference_is_reported_with_the_missing_one(tmp_path: Path) -> None:
    files = variant(
        {
            "Services/Services.csproj": references("Entities"),
            "Repositories/Repositories.csproj": references("Services", "Entities"),
        }
    )

    outcome = run("layer_references", solution(tmp_path, files), **REFERENCES)

    assert message(outcome) == (
        "The project references do not follow the layers: `Services` does not reference "
        "`Repositories` (services → repositories is required); `Repositories` references "
        "`Services` (repositories → services is not allowed). Total: 2."
    )


def test_entities_may_reference_no_layer(tmp_path: Path) -> None:
    files = variant({"Entities/Entities.csproj": references("Services")})

    outcome = run("layer_references", solution(tmp_path, files), **REFERENCES)

    assert "`Entities` references `Services` (entities → services is not allowed)" in message(
        outcome
    )


def test_a_reference_in_other_letter_case_is_a_reference(tmp_path: Path) -> None:
    api = FILES["Api/Api.csproj"].replace(
        "..\\Services\\Services.csproj", "..\\services\\services.csproj"
    )
    files = variant({"Api/Api.csproj": api})
    root = solution(tmp_path, files)
    evidence = LocalArchitectureEvidenceReader().scan(root)
    assert ProjectReferenceStatus.CASE_MISMATCHED in {
        reference.status for reference in evidence.project_references
    }

    assert run("layer_references", root, evidence, **REFERENCES) == ()


TRANSITIVE = {**REFERENCES, "transitive_required": True}


def test_a_required_reference_may_go_through_another_layer(tmp_path: Path) -> None:
    files = variant({"Services/Services.csproj": references("Repositories")})
    root = solution(tmp_path, files)

    assert run("layer_references", root, **TRANSITIVE) == ()
    assert message(run("layer_references", root, **REFERENCES)) == (
        "The project references do not follow the layers: `Services` does not reference "
        "`Entities` (services → entities is required). Total: 1."
    )


def test_a_transitive_required_reference_still_needs_a_path(tmp_path: Path) -> None:
    files = variant(
        {
            "Services/Services.csproj": references("Repositories"),
            "Repositories/Repositories.csproj": LIBRARY,
        }
    )

    outcome = run("layer_references", solution(tmp_path, files), **TRANSITIVE)

    assert message(outcome) == (
        "The project references do not follow the layers: `Services` doesn't reference "
        "`Entities`, directly or through another layer (services → entities is required); "
        "`Repositories` doesn't reference `Entities`, directly or through another layer "
        "(repositories → entities is required). Total: 2."
    )


def test_a_forbidden_reference_counts_only_when_direct(tmp_path: Path) -> None:
    through_services = run("layer_references", solution(tmp_path / "through"), **TRANSITIVE)
    files = variant({"Api/Api.csproj": references("Services", "Entities", "Repositories")})
    direct = run("layer_references", solution(tmp_path / "direct", files), **TRANSITIVE)

    assert through_services == ()
    assert message(direct) == (
        "The project references do not follow the layers: `Api` references `Repositories` "
        "(api → repositories is not allowed). Total: 1."
    )


def test_layer_references_cannot_tell_when_a_layer_has_no_project(tmp_path: Path) -> None:
    outcome = run("layer_references", solution(tmp_path, SINGLE_PROJECT), **REFERENCES)

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


# ---------------------------------------------------------------- code_only_in_layer


def test_file_access_in_the_repositories_project_passes(tmp_path: Path) -> None:
    repository = FILES["Repositories/UserRepository.cs"].replace(
        "Task.FromResult<User?>(null)", 'Task.FromResult<User?>(File.Exists("u") ? null : null)'
    )
    files = variant({"Repositories/UserRepository.cs": repository})

    assert run("code_only_in_layer", solution(tmp_path, files), **FILE_ACCESS) == ()


def test_file_access_in_a_controller_is_located(tmp_path: Path) -> None:
    controller = FILES[CONTROLLER].replace(
        "        var user = await _service.GetById(id);\n",
        "        // File.ReadAllText is only mentioned in this comment\n"
        '        var log = "File.Delete";\n'
        '        var text = await File.ReadAllTextAsync("users.txt");\n'
        "        var user = await _service.GetById(id);\n",
    )
    files = variant({CONTROLLER: controller})

    outcome = run("code_only_in_layer", solution(tmp_path, files), **FILE_ACCESS)

    assert message(outcome) == (
        "This code belongs only in the repositories layer, but it is also in other projects: "
        f"`File.ReadAllTextAsync` ({CONTROLLER}, line 23). Total: 1."
    )


def test_file_access_cannot_be_placed_without_a_repositories_project(tmp_path: Path) -> None:
    outcome = run("code_only_in_layer", solution(tmp_path, SINGLE_PROJECT), **FILE_ACCESS)

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


def test_file_access_cannot_tell_without_the_signals(tmp_path: Path) -> None:
    outcome = run("code_only_in_layer", solution(tmp_path), unavailable(), **FILE_ACCESS)

    assert outcome is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- implements_interface


@pytest.mark.parametrize("role", ["services", "repositories"])
def test_every_class_implements_an_interface(tmp_path: Path, role: str) -> None:
    assert run("implements_interface", solution(tmp_path), role=role) == ()


def test_a_service_without_an_interface_is_reported(tmp_path: Path) -> None:
    files = variant(
        {
            SERVICE: FILES[SERVICE].replace(") : IUserService", ")"),
            "Services/ServiceExtensions.cs": "public static class ServiceExtensions { }\n",
            "Services/Validation.cs": "public class UserValidator { }\n",
        }
    )

    outcome = run(
        "implements_interface", solution(tmp_path, files), role="services", exclude=["Validator$"]
    )

    assert message(outcome) == (
        "Classes in the services layer that implement no interface declared in the "
        f"repository: `UserService` ({SERVICE}, line 6). Total: 1."
    )


def test_an_interface_through_a_base_class_counts(tmp_path: Path) -> None:
    files = variant(
        {
            SERVICE: FILES[SERVICE].replace(") : IUserService", ") : ServiceBase"),
            "Services/ServiceBase.cs": "public abstract class ServiceBase : IUserService { }\n",
        }
    )

    assert run("implements_interface", solution(tmp_path, files), role="services") == ()


def test_a_class_declared_twice_is_not_guessed(tmp_path: Path) -> None:
    files = variant(
        {
            "Services/Plain.cs": "public class Plain { }\n",
            "Services/Other/Plain.cs": "public class Plain { }\n",
            "Services/Report.cs": "public class Report { }\n",
        }
    )

    outcome = run("implements_interface", solution(tmp_path, files), role="services")

    assert message(outcome) == (
        "Classes in the services layer that implement no interface declared in the "
        "repository: `Report` (Services/Report.cs, line 1). Total: 1. Not checked, because "
        "the name is declared more than once: `Plain`."
    )


def test_implements_interface_cannot_tell_without_the_signals(tmp_path: Path) -> None:
    outcome = run("implements_interface", tmp_path, unavailable(), role="services")

    assert outcome is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- constructor_takes_interface


def test_constructors_taking_interfaces_pass(tmp_path: Path) -> None:
    outcome = run(
        "constructor_takes_interface", solution(tmp_path), consumer="api", dependency="services"
    )

    assert outcome == ()


def test_a_controller_taking_the_service_class_is_told_the_interface(tmp_path: Path) -> None:
    files = variant({CONTROLLER: CONTROLLER_TAKES_CLASS})

    outcome = run(
        "constructor_takes_interface",
        solution(tmp_path, files),
        consumer="api",
        dependency="services",
    )

    assert message(outcome) == (
        "Constructors in the api layer take a services class instead of its interface: "
        f"`UsersController` takes `UserService`; take `IUserService` ({CONTROLLER}, line 13). "
        "Total: 1."
    )


def test_a_class_with_no_interface_is_left_to_implements_interface(tmp_path: Path) -> None:
    files = variant(
        {
            CONTROLLER: CONTROLLER_TAKES_CLASS,
            SERVICE: FILES[SERVICE].replace(") : IUserService", ")"),
        }
    )

    outcome = run(
        "constructor_takes_interface",
        solution(tmp_path, files),
        consumer="api",
        dependency="services",
    )

    assert outcome == ()


def test_constructor_takes_interface_cannot_tell_without_services(tmp_path: Path) -> None:
    outcome = run(
        "constructor_takes_interface",
        solution(tmp_path, SINGLE_PROJECT),
        consumer="api",
        dependency="services",
    )

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


# ---------------------------------------------------------------- interfaces_registered


def test_registered_interfaces_pass(tmp_path: Path) -> None:
    outcome = run("interfaces_registered", solution(tmp_path), consumers=["api", "services"])

    assert outcome == ()


def test_an_unregistered_interface_is_reported(tmp_path: Path) -> None:
    program = FILES[PROGRAM].replace(
        "builder.Services.AddScoped<IUserService, UserService>();\n", ""
    )
    files = variant({PROGRAM: program})

    outcome = run("interfaces_registered", solution(tmp_path, files), consumers=["api", "services"])

    assert message(outcome) == (
        "Constructors take an interface that is not registered (AddScoped, AddTransient or "
        f"AddSingleton with that interface): `UsersController` takes `IUserService` "
        f"({CONTROLLER}, line 13). Total: 1."
    )


def test_registrations_without_a_type_are_named_in_the_finding(tmp_path: Path) -> None:
    program = FILES[PROGRAM].replace(
        "builder.Services.AddScoped<IUserService, UserService>();\n",
        "builder.Services.AddScoped(provider => Create(provider));\n",
    )
    files = variant({PROGRAM: program})

    outcome = run("interfaces_registered", solution(tmp_path, files), consumers=["api"])

    assert message(outcome).endswith(
        "Total: 1. 1 registrations name no type (a factory or a key), so check these by hand."
    )


def test_interfaces_registered_cannot_tell_without_the_signals(tmp_path: Path) -> None:
    outcome = run("interfaces_registered", tmp_path, unavailable(), consumers=["api"])

    assert outcome is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- no_concrete_construction

CONSTRUCTION: dict[str, Any] = {"in": ["api", "services"], "of": ["services", "repositories"]}


def test_no_concrete_construction_passes(tmp_path: Path) -> None:
    assert run("no_concrete_construction", solution(tmp_path), **CONSTRUCTION) == ()


def test_a_service_creating_a_repository_is_reported_but_not_an_entity(tmp_path: Path) -> None:
    files = variant({SERVICE: SERVICE_CREATES})

    outcome = run("no_concrete_construction", solution(tmp_path, files), **CONSTRUCTION)

    assert message(outcome) == (
        "Classes create a services or repositories class with new instead of receiving its "
        f"interface through the constructor: `UserService` creates `UserRepository` ({SERVICE}, "
        "line 8). Total: 1."
    )


def test_a_factory_in_the_top_level_statements_is_not_read(tmp_path: Path) -> None:
    program = FILES[PROGRAM].replace(
        "builder.Services.AddScoped<IUserService, UserService>();\n",
        "builder.Services.AddScoped<IUserService>(p => new UserService(null!));\n",
    )
    files = variant({PROGRAM: program})

    assert run("no_concrete_construction", solution(tmp_path, files), **CONSTRUCTION) == ()


def test_no_concrete_construction_cannot_tell_without_repositories(tmp_path: Path) -> None:
    outcome = run("no_concrete_construction", solution(tmp_path, SINGLE_PROJECT), **CONSTRUCTION)

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


# ---------------------------------------------------------------- params


def test_a_role_not_defined_in_roles_is_a_params_problem() -> None:
    problems = REGISTRY.validate_params(
        "layer_projects", {"roles": ROLES, "required": ("api", "frontend")}
    )

    assert problems == ("param 'params': Value error, roles not defined in roles: frontend",)


def test_any_role_is_only_a_forbidden_target() -> None:
    problems = REGISTRY.validate_params(
        "layer_references", {"roles": ROLES, "required": (("entities", "*"),)}
    )

    assert problems
