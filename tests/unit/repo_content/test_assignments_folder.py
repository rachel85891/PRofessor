"""The committed assignments/ folder must always load and validate against the real checks."""

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import ExpectedJson, RequirementKind
from hw_checker.services.assignments import AssignmentCatalogService


def catalog() -> AssignmentCatalogService:
    source = YamlAssignmentSource(REPO_ROOT / "assignments")
    return AssignmentCatalogService(source, CheckRegistry(builtin_checks()))


def test_committed_assignments_folder_loads_general_and_hw01() -> None:
    assignments = catalog().list_assignments()

    assert [(a.id, a.order) for a in assignments] == [("_general", 0), ("hw01", 1)]
    general, hw01 = assignments
    assert [(r.id, r.severity.value) for r in general.requirements] == [
        ("gen-build", "critical"),
        ("gen-controllers", "minor"),
        ("gen-naming-types", "minor"),
        ("gen-naming-properties", "minor"),
        ("gen-naming-methods", "minor"),
        ("gen-naming-locals", "minor"),
        ("gen-file-names", "minor"),
        ("gen-controller-suffix", "minor"),
    ]
    assert [(r.id, r.severity.value) for r in hw01.requirements] == [
        ("hw01-get-missing", "major"),
        ("hw01-create", "critical"),
        ("hw01-read-back", "critical"),
        ("hw01-list", "major"),
        ("hw01-login", "major"),
        ("hw01-login-wrong", "minor"),
        ("hw01-login-wrong-204", "minor"),
        ("hw01-update", "major"),
    ]
    assert all(r.kind is RequirementKind.HTTP for r in hw01.requirements)


def test_hw01_content_checks_and_wrong_login_statuses() -> None:
    _, hw01 = catalog().list_assignments()
    probes = {r.id: r.http_probes[0] for r in hw01.requirements}

    assert probes["hw01-create"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-read-back"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-read-back"].expect_values == ("probe.user@example.com",)
    assert probes["hw01-list"].expect_json is ExpectedJson.ARRAY_OF_OBJECTS
    assert probes["hw01-login-wrong"].allowed_statuses == (204, 401, 404, 400)
    assert probes["hw01-login-wrong-204"].allowed_statuses == (204,)


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
    assert users == ["hw01-read-back", "hw01-update"]
