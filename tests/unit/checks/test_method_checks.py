"""`methods_async` (HW05-G5) on the four-project sample solution and its variants, with made-up
requirements: nothing of HW05 is in the check."""

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
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
SERVICES = ["services"]


def service(body: str, header: str = "public class UserService : IUserService") -> dict[str, str]:
    """Services/UserService.cs holding one class with `body`."""
    text = f"namespace Services;\n\n{header}\n{{\n{body}\n}}\n"
    return {"Services/UserService.cs": text}


def run(
    root: Path,
    *,
    evidence: ArchitectureEvidence | None = None,
    roles_in: list[str] = SERVICES,
    **params: Any,
) -> CheckResult:
    requirement = Requirement(
        id="hw-req",
        title="Async methods",
        severity=Severity.MAJOR,
        kind=RequirementKind.STRUCTURE,
        check="methods_async",
        params={"roles": ROLES, "in": tuple(roles_in), **params},
    )
    registered = REGISTRY.get("methods_async")
    model = registered.spec.params_model.model_validate(dict(requirement.params))
    check_input = CheckInput(
        repo_dir=root,
        assignment=Assignment(id="hw05", order=5, title="Async", requirements=(requirement,)),
        requirement=requirement,
        architecture=evidence or LocalArchitectureEvidenceReader().scan(root),
    )
    return registered.function(check_input, model)


def interface(member: str) -> str:
    return f"namespace Services;\npublic interface IUserService\n{{\n    {member}\n}}\n"


PRODUCT = "namespace Services;\npublic class Product\n{\n    public int Sum() => 1;\n}\n"


def finding(outcome: CheckResult) -> Finding:
    assert isinstance(outcome, tuple), outcome
    [found] = outcome
    return found


def solution(tmp_path: Path, changed: dict[str, str], **kwargs: Any) -> Path:
    return write_solution(tmp_path, variant(changed, **kwargs))


def test_an_async_task_method_passes(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public async Task<IActionResult> Get() { }"))

    assert run(root) == ()


def test_a_task_method_without_async_names_the_modifier(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public Task<User> Get() => null;"))

    found = finding(run(root))

    assert "`UserService`.`Get` isn't marked `async` (Services/UserService.cs, line 5)" in (
        found.message_en
    )
    assert "returns" not in found.message_en
    assert "Total: 1." in found.message_en
    assert HEBREW.search(found.message_he)
    assert found.evidence is not None
    assert (found.evidence.file_path, found.evidence.line_start) == ("Services/UserService.cs", 5)
    assert found.evidence.quote == "public Task<User> Get() => null;"


def test_a_method_returning_a_plain_type_names_the_return_type(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public User Get() { }"))

    found = finding(run(root))

    assert "`Get` returns `User`, not a `Task` and isn't marked `async`" in found.message_en
    assert "וגם" in found.message_he


def test_async_void_fails(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public async void Run() { }"))

    found = finding(run(root))

    assert "`Run` returns `void`, not a `Task`" in found.message_en
    assert "isn't marked" not in found.message_en


@pytest.mark.parametrize(
    "return_type", ["Task", "ValueTask", "ValueTask<User>", "System.Threading.Tasks.Task<int>"]
)
def test_every_task_shape_is_accepted(tmp_path: Path, return_type: str) -> None:
    root = solution(tmp_path, service(f"    public async {return_type} Get() {{ }}"))

    assert run(root) == ()


def test_a_private_or_internal_helper_is_not_read(tmp_path: Path) -> None:
    body = (
        "    User Helper() { }\n    private User Other() { }\n    internal User Third() { }\n"
        "    public async Task Get() { }"
    )
    root = solution(tmp_path, service(body))

    assert run(root) == ()


def test_interface_members_need_only_the_task_return_type(tmp_path: Path) -> None:
    ok = solution(
        tmp_path / "ok",
        {"Services/IUserService.cs": interface("Task<User> Get();")},
    )
    bad = solution(
        tmp_path / "bad",
        {"Services/IUserService.cs": interface("User Get();")},
    )

    assert run(ok, exclude=["^UserService$"]) == ()
    found = finding(run(bad, exclude=["^UserService$"]))
    assert "`IUserService`.`Get` returns `User`, not a `Task` (" in found.message_en
    assert "isn't marked" not in found.message_en


def test_an_abstract_method_is_only_asked_for_the_return_type(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        service("    public abstract Task Get();", "public abstract class UserService"),
    )

    assert run(root) == ()


def test_require_returns_task_alone(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public Task<User> Get() => null;"))

    assert run(root, require=["returns_task"]) == ()
    assert "isn't marked" in finding(run(root, require=["async_modifier"])).message_en


def test_exclude_methods_leaves_out_by_name(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public void Main() { }\n    public void Dispose() { }"))

    assert "Total: 2." in finding(run(root)).message_en
    assert run(root, exclude_methods=["^Main$", "^Dispose$"]) == ()
    assert "Total: 1." in finding(run(root, exclude_methods=["^Main$"])).message_en


def test_exclude_modifiers_leaves_out_by_modifier(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        service('    public override string ToString() => "";\n    public static void Go() { }'),
    )

    assert run(root, exclude_modifiers=["override", "static"]) == ()
    assert "Total: 1." in finding(run(root, exclude_modifiers=["static"])).message_en


def test_exclude_leaves_out_a_class_by_name(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public void Run() { }"))

    assert run(root, exclude=["^UserService$"]) == ()


def test_exclude_bases_leaves_out_a_class_by_its_base(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        service("    public override void Run() { }", "public class UserService : DbContext"),
    )

    assert finding(run(root)) is not None
    assert run(root, exclude_bases=["^DbContext$"]) == ()


def test_exclude_db_entities_leaves_out_a_contexts_entities(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {
            **service(
                "    public DbSet<Product> Products { get; set; }", "public class Ctx : DbContext"
            ),
            "Services/Product.cs": PRODUCT,
        },
    )

    assert "`Product`.`Sum`" in finding(run(root, exclude_bases=["^DbContext$"])).message_en
    left_out = run(root, exclude_bases=["^DbContext$"], exclude_db_entities=True)
    assert left_out == ()


def test_the_finding_lists_five_examples_and_the_total(tmp_path: Path) -> None:
    body = "\n".join(f"    public int M{i}() {{ }}" for i in range(7))
    root = solution(tmp_path, service(body))

    found = finding(run(root))

    assert found.message_en.count("`UserService`.") == 5
    assert "Total: 7." in found.message_en
    assert "M4" in found.message_en
    assert "M5" not in found.message_en


def test_methods_of_other_roles_are_not_read(tmp_path: Path) -> None:
    root = solution(tmp_path, service("    public int Sync() { }"))

    assert run(root, roles_in=["api"]) == ()
    assert finding(run(root, roles_in=["api", "services"])) is not None


def test_no_class_in_the_roles_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(
        tmp_path,
        {},
        removed=("Services/UserService.cs", "Services/IUserService.cs"),
    )

    assert run(root) is NotEvaluatedReason.DEPENDENCY_FAILED


def test_a_role_with_no_project_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path, {})

    assert run(root, roles={"x": {"name": "nothing"}}, roles_in=["x"]) is (
        NotEvaluatedReason.DEPENDENCY_FAILED
    )


def test_evidence_unavailable_is_not_evaluated(tmp_path: Path) -> None:
    root = solution(tmp_path, {})
    coverage = EvidenceCoverage(status=EvidenceCoverageStatus.UNAVAILABLE, files_examined=0)
    unavailable = ArchitectureEvidence(inventory_coverage=coverage, signal_coverage=coverage)

    assert run(root, evidence=unavailable) is NotEvaluatedReason.EVIDENCE_UNAVAILABLE


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"in": []},
        {"in": ["missing"]},
        {"in": ["services"], "exclude_methods": ["("]},
        {"in": ["services"], "exclude": ["["]},
        {"in": ["services"], "require": ["fast"]},
        {"in": ["services"], "require": []},
        {"in": ["services"], "exclude_modifiers": ["fast"]},
        {"in": ["services"], "extra": 1},
    ],
)
def test_bad_params_are_refused(params: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        REGISTRY.get("methods_async").spec.params_model.model_validate({"roles": ROLES, **params})


def test_the_check_is_registered_with_its_params_schema() -> None:
    spec = REGISTRY.get("methods_async").spec
    schema = spec.params_model.model_json_schema(by_alias=True)

    assert spec.reads_architecture
    assert {
        "in",
        "exclude",
        "exclude_bases",
        "exclude_db_entities",
        "exclude_methods",
        "exclude_modifiers",
        "require",
    } <= set(schema["properties"])
