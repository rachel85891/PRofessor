"""HW02 or HW03 per repo, end to end through the check service with the real hw02/hw03 data
and the real evidence reader (stage 21), on three synthetic repos; and the fixer's target
and notes-only guard for each."""

from pathlib import Path

import pytest

from hw_checker.adapters.architecture_evidence import LocalArchitectureEvidenceReader
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    NotEvaluated,
    NotEvaluatedReason,
)
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.fixing import FixService, fix_target
from tests.unit.domain import builders
from tests.unit.layered_solution import FILES, variant, write_solution
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    BREAK_FIX,
    CONTROLLER,
    REPO,
    RESULTS,
    RUN_ID,
    SOURCE,
    FixerSetup,
)

HW02 = next(a for a in SOURCE.load_all() if a.id == "hw02")
HW03 = next(a for a in SOURCE.load_all() if a.id == "hw03")
SERVICE = DeterministicCheckService(
    DeterministicRequirementChecker(CheckRegistry(builtin_checks())),
    architecture=LocalArchitectureEvidenceReader(),
)

ONE_PROJECT = {
    "App/App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
    "App/Program.cs": "var app = WebApplication.CreateBuilder(args).Build();\napp.Run();\n",
    "App/Controllers/UsersController.cs": (
        "public class UsersController : ControllerBase\n"
        "{\n"
        '    public string Get() => File.ReadAllText("users.txt");\n'
        "}\n"
    ),
}
LAYERS_WITHOUT_DI = {
    path: text
    for path, text in FILES.items()
    if path.endswith(".csproj") or path in {"Entities/User.cs", "Api/Program.cs"}
} | {
    "Api/Program.cs": (
        "var builder = WebApplication.CreateBuilder(args);\n"
        "builder.Services.AddControllers();\n"
        "builder.Build().Run();\n"
    ),
    "Repositories/UserRepository.cs": (
        "using Entities;\n"
        "namespace Repositories;\n"
        "public class UserRepository\n"
        "{\n"
        '    public string[] All() => File.ReadAllLines("users.txt");\n'
        "}\n"
    ),
    "Services/UserService.cs": (
        "using Repositories;\n"
        "namespace Services;\n"
        "public class UserService\n"
        "{\n"
        "    private readonly UserRepository _repository = new UserRepository();\n"
        "    public string[] All() => _repository.All();\n"
        "}\n"
    ),
    "Api/Controllers/UsersController.cs": (
        "using Services;\n"
        "namespace Api.Controllers;\n"
        "public class UsersController : ControllerBase\n"
        "{\n"
        "    private readonly UserService _service = new UserService();\n"
        "}\n"
    ),
}
DI_WITHOUT_A_REGISTRATION = variant(
    {
        "Api/Program.cs": FILES["Api/Program.cs"].replace(
            "builder.Services.AddScoped<IUserService, UserService>();\n", ""
        )
    }
)


def evaluate(root: Path) -> dict[str, AssignmentResult]:
    return {assignment.id: SERVICE.evaluate(assignment, root, None) for assignment in (HW02, HW03)}


def not_applicable(results: AssignmentResult) -> bool:
    return results.findings == () and all(
        entry.reason is NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE
        for entry in results.not_evaluated
    )


def test_a_single_project_repo_gets_one_layers_finding(tmp_path: Path) -> None:
    results = evaluate(write_solution(tmp_path, ONE_PROJECT))

    hw02 = results["hw02"]
    assert [finding.requirement_id for finding in hw02.findings] == ["hw02-layer-projects"]
    assert hw02.not_evaluated == tuple(
        NotEvaluated(
            requirement_id=requirement_id,
            reason=NotEvaluatedReason.DEPENDENCY_FAILED,
            detail="`hw02-layer-projects`",
        )
        for requirement_id in ("hw02-references", "hw02-file-access-in-repositories")
    )
    assert not_applicable(results["hw03"])
    assert fix_target((ASSIGNMENT, HW02, HW03), tuple(results.values())).short_name == "HW2"


def test_layers_without_di_are_on_the_hw02_path(tmp_path: Path) -> None:
    results = evaluate(write_solution(tmp_path, LAYERS_WITHOUT_DI))

    assert results["hw02"].findings == ()
    assert results["hw02"].not_evaluated == ()
    assert not_applicable(results["hw03"])
    assert results["hw03"].not_evaluated[0].detail.startswith("applies_when di_started: ")
    assert fix_target((ASSIGNMENT, HW02, HW03), tuple(results.values())).short_name == "HW2"


def test_di_with_a_missing_registration_is_on_the_hw03_path(tmp_path: Path) -> None:
    results = evaluate(write_solution(tmp_path, DI_WITHOUT_A_REGISTRATION))

    hw03 = results["hw03"]
    assert [finding.requirement_id for finding in hw03.findings] == ["hw03-registered"]
    assert hw03.not_evaluated == ()
    assert not_applicable(results["hw02"])
    assert fix_target((ASSIGNMENT, HW02, HW03), tuple(results.values())).short_name == "HW3"


@pytest.mark.parametrize(
    ("target", "requirement_id"),
    [(HW02, r.id) for r in HW02.requirements] + [(HW03, r.id) for r in HW03.requirements],
)
def test_a_fix_claiming_an_hw02_or_hw03_requirement_is_rejected(
    tmp_path: Path, target: Assignment, requirement_id: str
) -> None:
    finding = builders.finding(
        id=f"{requirement_id}-abc",
        requirement_id=requirement_id,
        assignment_id=target.id,
        evidence=builders.evidence(file_path=CONTROLLER, line_start=10, line_end=12),
    )
    results = (*RESULTS, AssignmentResult(assignment_id=target.id, findings=(finding,)))
    selected = (ASSIGNMENT, HW02, HW03)
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, selected, results, REPO, setup.fetched)
    claimed = BREAK_FIX.model_copy(update={"finding_ids": (requirement_id,)})

    application = setup.service.apply(workspace, [claimed], target, selected, results)

    assert application.applied == ()
    assert FixService.feedback(application, None) == [
        f"Rejected fix in {CONTROLLER}: {requirement_id} allows notes only; plan a note, not a fix"
    ]
