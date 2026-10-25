"""The committed assignments/ folder must always load and validate against the real checks."""

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import RequirementKind
from hw_checker.services.assignments import AssignmentCatalogService


def catalog() -> AssignmentCatalogService:
    source = YamlAssignmentSource(REPO_ROOT / "assignments")
    return AssignmentCatalogService(source, CheckRegistry(builtin_checks()))


def test_committed_assignments_folder_loads_general_and_hw01() -> None:
    assignments = catalog().list_assignments()

    assert [(a.id, a.order) for a in assignments] == [("_general", 0), ("hw01", 1)]
    general, hw01 = assignments
    assert [r.id for r in general.requirements] == ["gen-build", "gen-controllers"]
    assert [r.id for r in hw01.requirements] == [
        "hw01-list",
        "hw01-get-missing",
        "hw01-create",
        "hw01-read-back",
        "hw01-login",
        "hw01-login-wrong",
        "hw01-update",
        "hw01-delete",
    ]
    assert all(r.kind is RequirementKind.HTTP for r in hw01.requirements)


def test_committed_requirements_validate_against_the_builtin_checks() -> None:
    assert catalog().validate_all() == ()


def test_round_for_hw01_selects_general_and_hw01() -> None:
    assert [a.id for a in catalog().select_for_round("hw01")] == ["_general", "hw01"]


def test_the_created_user_id_is_captured_before_it_is_used() -> None:
    _, hw01 = catalog().list_assignments()
    probes = [p for r in hw01.requirements for p in r.http_probes]

    (capturing,) = [p for p in probes if p.capture is not None]
    assert capturing.requirement_id == "hw01-create"
    assert capturing.capture is not None
    assert (capturing.capture.name, capturing.capture.source) == ("user_id", "body:id")
    users = [p.requirement_id for p in probes if "user_id" in p.variables_used]
    assert users == ["hw01-read-back", "hw01-update", "hw01-delete", "hw01-delete"]
