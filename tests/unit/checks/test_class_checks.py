"""`classes_in_role` and `namespace_matches_project` (HW05-G3) on the four-project sample
solution and its variants, with made-up requirements: nothing of HW05 is in the checks."""

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.class_checks import project_namespace
from hw_checker.checks.registry import CheckRegistry, CheckResult
from hw_checker.domain import (
    ArchitectureEvidence,
    Assignment,
    CheckInput,
    EvidenceCoverage,
    EvidenceCoverageStatus,
    Finding,
    NotEvaluatedReason,
    Requirement,
    RequirementKind,
    Severity,
)
from tests.unit.layered_solution import variant, write_solution

HEBREW = re.compile(r"[֐-׿]")
ROLES: dict[str, Any] = {
    "api": {"sdk": "Microsoft.NET.Sdk.Web", "name": "api|web"},
    "entities": {"name": "entit|model|dto|domain"},
    "services": {"name": r"servic|business|\bbl\b|logic"},
    "repositories": {"name": r"repositor|\bdal\b|data|access"},
}
REGISTRY = CheckRegistry(builtin_checks())
LIBRARY = '<Project Sdk="Microsoft.NET.Sdk" />\n'
CONTEXT = (
    "using Microsoft.EntityFrameworkCore;\n"
    "namespace {namespace};\n"
    "public class ShopContext : DbContext\n"
    "{{\n"
    "    public DbSet<User> Users {{ get; set; }}\n"
    "}}\n"
)
ENTITIES_OF_CONTEXT: dict[str, Any] = {"entities_of_bases": ["^DbContext$"]}
THE_CONTEXT: dict[str, Any] = {"bases": ["^DbContext$"]}
THE_USER: dict[str, Any] = {"names": ["^User$"]}


def run(
    check: str,
    root: Path,
    evidence: ArchitectureEvidence | None = None,
    **params: Any,
) -> CheckResult:
    requirement = Requirement(
        id="hw-req",
        title="A placement requirement",
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check=check,
        params={"roles": ROLES, **params},
    )
    registered = REGISTRY.get(check)
    model = registered.spec.params_model.model_validate(dict(requirement.params))
    check_input = CheckInput(
        repo_dir=root,
        assignment=Assignment(id="hw05", order=5, title="Placement", requirements=(requirement,)),
        requirement=requirement,
        architecture=evidence or LocalArchitectureEvidenceReader().scan(root),
    )
    return registered.function(check_input, model)


def in_role(root: Path, select: dict[str, Any], role: str = "entities") -> CheckResult:
    return run("classes_in_role", root, role=role, select=select)


def namespace(root: Path, select: dict[str, Any], **params: Any) -> CheckResult:
    return run("namespace_matches_project", root, select=select, **params)


def finding(outcome: CheckResult) -> Finding:
    assert isinstance(outcome, tuple), outcome
    [found] = outcome
    return found


def unavailable() -> ArchitectureEvidence:
    coverage = EvidenceCoverage(status=EvidenceCoverageStatus.UNAVAILABLE, files_examined=0)
    return ArchitectureEvidence(inventory_coverage=coverage, signal_coverage=coverage)


def solution(tmp_path: Path, changed: dict[str, str] | None = None, **kwargs: Any) -> Path:
    return write_solution(tmp_path, variant(changed, **kwargs))


# ---------------------------------------------------------------- classes_in_role


def test_classes_all_in_the_role_pass(tmp_path: Path) -> None:
    root = solution(tmp_path)

    assert in_role(root, THE_USER) == ()


def test_a_selected_class_in_a_project_of_another_role_is_a_finding(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {"Api/Models/User.cs": "namespace Api.Models;\npublic class User { }\n"},
        removed=("Entities/User.cs",),
    )

    found = finding(in_role(root, THE_USER))

    assert "`User` is in the project `Api` (api layer) (Api/Models/User.cs, line 2)" in (
        found.message_en
    )
    assert "belong in the entities layer's project" in found.message_en
    assert "Total: 1." in found.message_en
    assert HEBREW.search(found.message_he)
    assert found.evidence is not None
    assert (found.evidence.file_path, found.evidence.line_start) == ("Api/Models/User.cs", 2)
    assert found.evidence.quote == "public class User { }"


def test_a_context_in_the_repositories_project_passes_and_in_the_api_fails(
    tmp_path: Path,
) -> None:
    in_repositories = solution(
        tmp_path / "ok", {"Repositories/ShopContext.cs": CONTEXT.format(namespace="Repositories")}
    )
    in_api = solution(tmp_path / "bad", {"Api/ShopContext.cs": CONTEXT.format(namespace="Api")})

    assert in_role(in_repositories, THE_CONTEXT, "repositories") == ()
    found = finding(in_role(in_api, THE_CONTEXT, "repositories"))
    assert "`ShopContext` is in the project `Api` (api layer)" in found.message_en


def test_entities_in_entities_pass_and_in_a_models_folder_of_the_api_fail(
    tmp_path: Path,
) -> None:
    context = CONTEXT.format(namespace="Repositories")
    inside = solution(tmp_path / "ok", {"Repositories/ShopContext.cs": context})
    outside = solution(
        tmp_path / "bad",
        {
            "Repositories/ShopContext.cs": context,
            "Api/Models/User.cs": "namespace Api.Models;\npublic class User { }\n",
        },
        removed=("Entities/User.cs",),
    )

    assert in_role(inside, ENTITIES_OF_CONTEXT) == ()
    assert (
        "`User` is in the project `Api`"
        in finding(in_role(outside, ENTITIES_OF_CONTEXT)).message_en
    )


def test_a_class_in_an_unassigned_project_is_a_finding(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {
            "Helpers/Helpers.csproj": LIBRARY,
            "Helpers/User.cs": "namespace Helpers;\npublic class User { }\n",
        },
        removed=("Entities/User.cs",),
    )

    found = finding(in_role(root, THE_USER))

    assert "`User` is in the project `Helpers`, which is in no layer" in found.message_en


def test_a_class_in_no_project_is_a_finding(tmp_path: Path) -> None:
    root = solution(
        tmp_path, {"Loose.cs": "public class User { }\n"}, removed=("Entities/User.cs",)
    )

    found = finding(in_role(root, THE_USER))

    assert "`User` is in no project (Loose.cs, line 1)" in found.message_en


def test_the_finding_lists_five_examples_and_the_total(tmp_path: Path) -> None:
    classes = "".join(f"public class Item{i} {{ }}\n" for i in range(7))
    root = solution(tmp_path, {"Api/Items.cs": f"namespace Api;\n{classes}"})

    found = finding(in_role(root, {"names": ["^Item"]}))

    assert found.message_en.count(" is in the project ") == 5
    assert "Total: 7." in found.message_en
    assert "Item4" in found.message_en
    assert "Item5" not in found.message_en


def test_no_class_selected_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path)

    assert in_role(root, {"names": ["^Missing$"]}) is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_role_with_no_project_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        removed=(
            "Entities/Entities.csproj",
            "Entities/User.cs",
            "Entities/IClock.cs",
            "Entities/SystemClock.cs",
        ),
    )

    assert in_role(root, THE_USER) is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_name_declared_twice_is_left_out_and_said(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {
            "Api/User.cs": "namespace Api;\npublic class User { }\n",
            "Api/Audit.cs": "namespace Api;\npublic class Audit { }\n",
        },
    )

    found = finding(in_role(root, {"names": ["^User$", "^Audit$"]}))

    assert "`Audit` is in the project `Api`" in found.message_en
    assert "`User` is in" not in found.message_en
    assert "the name is declared more than once: `User`" in found.message_en


def test_evidence_unavailable_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path)

    outcome = in_role(root, THE_USER)
    assert outcome == ()
    result = run("classes_in_role", root, unavailable(), role="entities", select=THE_USER)
    assert result is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- namespace_matches_project


def entity(path: str, declared: str) -> dict[str, str]:
    body = f"namespace {declared};\n" if declared else ""
    return {path: f"{body}public class User {{ }}\n"}


def test_a_class_in_the_project_folder_with_the_projects_namespace_passes(
    tmp_path: Path,
) -> None:
    root = solution(tmp_path, entity("Entities/User.cs", "Entities"))

    assert namespace(root, THE_USER) == ()


def test_a_subfolder_is_fine_when_the_namespace_is_the_projects(tmp_path: Path) -> None:
    root = solution(
        tmp_path, entity("Entities/Models/User.cs", "Entities"), removed=("Entities/User.cs",)
    )

    assert namespace(root, THE_USER) == ()


def test_a_subfolder_segment_in_the_namespace_is_a_finding(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        entity("Entities/Models/User.cs", "Entities.Models"),
        removed=("Entities/User.cs",),
    )

    found = finding(namespace(root, THE_USER))

    assert (
        "`User` is in namespace `Entities.Models`, but the project `Entities` expects "
        "`Entities` (Entities/Models/User.cs, line 2)"
    ) in found.message_en
    assert HEBREW.search(found.message_he)
    assert found.evidence is not None
    assert found.evidence.line_start == 2


def test_an_extra_segment_before_is_a_finding(tmp_path: Path) -> None:
    root = solution(tmp_path, entity("Entities/User.cs", "MyApi.Entities"))

    found = finding(namespace(root, THE_USER))

    assert "in namespace `MyApi.Entities`, but the project `Entities` expects `Entities`" in (
        found.message_en
    )


def test_a_class_in_the_global_namespace_is_a_finding(tmp_path: Path) -> None:
    root = solution(tmp_path, entity("Entities/User.cs", ""))

    found = finding(namespace(root, THE_USER))

    assert "`User` has no namespace, but the project `Entities` expects `Entities`" in (
        found.message_en
    )
    assert found.evidence is not None
    assert found.evidence.quote == "public class User { }"


def test_block_namespaces_are_read(tmp_path: Path) -> None:
    ok = solution(
        tmp_path / "ok",
        {"Entities/User.cs": "namespace Entities\n{\n    public class User { }\n}\n"},
    )
    nested = solution(
        tmp_path / "nested",
        {
            "Entities/User.cs": "namespace Entities\n{\n    namespace Models\n    {\n"
            "        public class User { }\n    }\n}\n"
        },
    )

    assert namespace(ok, THE_USER) == ()
    assert "in namespace `Entities.Models`" in finding(namespace(nested, THE_USER)).message_en


def test_a_root_namespace_is_the_projects_namespace(tmp_path: Path) -> None:
    project = (
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        "<RootNamespace>Shop.Core</RootNamespace></PropertyGroup></Project>\n"
    )
    ok = solution(
        tmp_path / "ok",
        {"Entities/Entities.csproj": project, **entity("Entities/User.cs", "Shop.Core")},
    )
    bad = solution(
        tmp_path / "bad",
        {"Entities/Entities.csproj": project, **entity("Entities/User.cs", "Entities")},
    )

    assert namespace(ok, THE_USER) == ()
    assert "expects `Shop.Core`" in finding(namespace(bad, THE_USER)).message_en


def test_a_project_name_with_a_dot_is_the_namespace(tmp_path: Path) -> None:
    files = {
        "Shop.Entities/Shop.Entities.csproj": LIBRARY,
        **entity("Shop.Entities/User.cs", "Shop.Entities"),
    }
    ok = write_solution(tmp_path / "ok", {"Api/Api.csproj": LIBRARY, **files})
    bad = write_solution(
        tmp_path / "bad",
        {"Api/Api.csproj": LIBRARY, **files, **entity("Shop.Entities/User.cs", "Entities")},
    )

    assert namespace(ok, THE_USER) == ()
    assert "expects `Shop.Entities`" in finding(namespace(bad, THE_USER)).message_en


@pytest.mark.parametrize(
    ("project_file", "expected"),
    [
        ("My Entities/My Entities.csproj", "My_Entities"),
        ("My-Entities/My-Entities.csproj", "My_Entities"),
        ("2Entities/2Entities.csproj", "_2Entities"),
        ("Shop.Core-Data/Shop.Core-Data.csproj", "Shop.Core_Data"),
    ],
)
def test_the_default_namespace_replaces_characters_a_namespace_cannot_hold(
    project_file: str, expected: str
) -> None:
    assert project_namespace(project_file, None) == expected


def test_a_project_with_a_space_or_a_dash_uses_the_replaced_name(tmp_path: Path) -> None:
    root = write_solution(
        tmp_path,
        {
            "Api/Api.csproj": LIBRARY,
            "My-Entities/My-Entities.csproj": LIBRARY,
            **entity("My-Entities/User.cs", "My_Entities"),
        },
    )

    assert namespace(root, THE_USER, roles={**ROLES, "entities": {"name": "entities"}}) == ()


def test_role_limits_the_check_to_that_roles_projects(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {
            **entity("Entities/User.cs", "Wrong"),
            "Services/Account.cs": "namespace Other;\npublic class Account { }\n",
        },
    )
    everything = {"names": ["^User$", "^Account$"]}

    assert "Total: 2." in finding(namespace(root, everything)).message_en
    limited = finding(namespace(root, everything, role="entities"))
    assert "Total: 1." in limited.message_en
    assert "`Account`" not in limited.message_en


def test_a_class_in_an_unassigned_project_is_judged_by_that_projects_namespace(
    tmp_path: Path,
) -> None:
    root = solution(
        tmp_path,
        {
            "Helpers/Helpers.csproj": LIBRARY,
            "Helpers/Tool.cs": "namespace Wrong;\npublic class Tool { }\n",
        },
    )

    found = finding(namespace(root, {"names": ["^Tool$"]}))

    assert "the project `Helpers` expects `Helpers`" in found.message_en


def test_no_class_selected_or_none_in_scope_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path, {"Loose.cs": "public class Loose { }\n"})

    assert namespace(root, {"names": ["^Missing$"]}) is NotEvaluatedReason.DEPENDENCY_FAILED
    assert namespace(root, {"names": ["^Loose$"]}) is NotEvaluatedReason.DEPENDENCY_FAILED
    assert namespace(root, THE_USER, role="services") is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_role_with_no_project_is_not_evaluated_for_namespaces(tmp_path: Path) -> None:
    root = solution(tmp_path)

    outcome = namespace(root, THE_USER, roles={"x": {"name": "nothing"}}, role="x")

    assert outcome is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_file_whose_namespace_cannot_be_read_is_not_judged(tmp_path: Path) -> None:
    root = solution(
        tmp_path, {"Entities/User.cs": "namespace A;\nnamespace B;\npublic class User { }\n"}
    )

    assert namespace(root, THE_USER) is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_name_declared_twice_is_left_out_of_the_namespace_check(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {
            **entity("Entities/Models/User.cs", "Entities.Models"),
            **entity("Services/User.cs", "Services"),
            "Entities/Tag.cs": "namespace Wrong;\npublic class Tag { }\n",
        },
    )

    found = finding(namespace(root, {"names": ["^User$", "^Tag$"]}))

    assert "`Tag` is in namespace `Wrong`" in found.message_en
    assert "the name is declared more than once: `User`" in found.message_en


def test_namespace_evidence_unavailable_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path)

    result = run("namespace_matches_project", root, unavailable(), select=THE_USER)

    assert result is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


# ---------------------------------------------------------------- params and catalog


@pytest.mark.parametrize(
    ("check", "params"),
    [
        ("classes_in_role", {"role": "entities"}),
        ("classes_in_role", {"role": "entities", "select": {}}),
        ("classes_in_role", {"role": "services", "select": THE_USER, "roles": {}}),
        ("classes_in_role", {"role": "missing", "select": THE_USER}),
        ("classes_in_role", {"role": "Entities", "select": THE_USER}),
        ("classes_in_role", {"role": "entities", "select": {"names": ["("]}}),
        ("classes_in_role", {"role": "entities", "select": THE_USER, "extra": 1}),
        ("namespace_matches_project", {}),
        ("namespace_matches_project", {"select": {}}),
        ("namespace_matches_project", {"select": THE_USER, "role": "missing"}),
        ("namespace_matches_project", {"select": {"bases": ["["]}}),
    ],
)
def test_bad_params_are_refused(check: str, params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        REGISTRY.get(check).spec.params_model.model_validate({"roles": ROLES, **params})


def test_both_checks_are_registered_with_their_params_schema() -> None:
    for name in ("classes_in_role", "namespace_matches_project"):
        spec = REGISTRY.get(name).spec
        schema = spec.params_model.model_json_schema()
        assert spec.reads_architecture
        assert "select" in schema["properties"]
        assert "ClassSelector" in schema["$defs"]
        assert {"names", "bases", "entities_of_bases"} <= set(
            schema["$defs"]["ClassSelector"]["properties"]
        )
