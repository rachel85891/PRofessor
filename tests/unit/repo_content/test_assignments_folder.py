"""The committed assignments/ folder must always load and validate against the real checks."""

import json
import string
from collections.abc import Mapping

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import ExpectedJson, Feedback, RequirementKind, Severity
from hw_checker.services.assignments import AssignmentCatalogService


def catalog() -> AssignmentCatalogService:
    source = YamlAssignmentSource(REPO_ROOT / "assignments", REPO_ROOT / "course")
    return AssignmentCatalogService(source, CheckRegistry(builtin_checks()))


def test_committed_assignments_folder_loads_general_and_hw01() -> None:
    assignments = catalog().list_assignments()

    assert [(a.id, a.order) for a in assignments] == [
        ("_general", 0),
        ("hw01", 1),
        ("hw02", 2),
        ("hw03", 3),
    ]
    general, hw01, *_ = assignments
    assert [(r.id, r.severity.value) for r in general.requirements] == [
        ("gen-build", "critical"),
        ("gen-controllers", "minor"),
        ("gen-naming-types", "minor"),
        ("gen-naming-properties", "minor"),
        ("gen-naming-methods", "minor"),
        ("gen-naming-locals", "minor"),
        ("gen-file-names", "minor"),
        ("gen-controller-suffix", "minor"),
        ("gen-gitignore", "minor"),
        ("gen-no-build-output", "minor"),
        ("gen-no-absolute-paths", "major"),
    ]
    assert [(r.id, r.severity.value) for r in hw01.requirements] == [
        ("hw01-get-missing", "major"),
        ("hw01-create", "critical"),
        ("hw01-read-back", "critical"),
        ("hw01-login", "major"),
        ("hw01-login-returns-user", "minor"),
        ("hw01-login-wrong", "minor"),
        ("hw01-login-wrong-204", "minor"),
        ("hw01-update", "major"),
        ("hw01-update-read-back", "major"),
        ("hw01-client-pages", "major"),
        ("hw01-client-scripts", "major"),
        ("hw01-client-file-refs", "major"),
        ("hw01-client-element-ids", "major"),
        ("hw01-client-fetch-routes", "major"),
    ]
    client = [r for r in hw01.requirements if r.id.startswith("hw01-client")]
    assert all(r.kind is RequirementKind.HTTP for r in hw01.requirements if r not in client)
    assert {r.check for r in client} == {
        "file_exists",
        "client_file_refs",
        "client_element_ids",
        "client_fetch_routes",
    }


def test_hw01_content_checks_and_wrong_login_statuses() -> None:
    _, hw01, *_ = catalog().list_assignments()
    probes = {r.id: r.http_probes[0] for r in hw01.requirements if r.http_probes}

    assert probes["hw01-create"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-read-back"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-read-back"].expect_values == ("probe.user@example.com",)
    assert probes["hw01-update"].expected_status == 200
    assert probes["hw01-update"].allowed_statuses == (200, 204)
    assert probes["hw01-login-wrong"].allowed_statuses == (204, 401, 404, 400)
    assert probes["hw01-login-wrong-204"].allowed_statuses == (204,)
    assert probes["hw01-login-returns-user"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-login-returns-user"].expect_values == ("{user_id}",)
    assert probes["hw01-update-read-back"].expect_json is ExpectedJson.OBJECT
    assert probes["hw01-update-read-back"].expect_values == (
        "probe.updated@example.com",
        "{user_id}",
    )


def test_hw01_positive_passwords_and_dependencies() -> None:
    _, hw01, *_ = catalog().list_assignments()
    requirements = {r.id: r for r in hw01.requirements}
    positive = ("hw01-create", "hw01-login", "hw01-login-returns-user", "hw01-update")
    wrong = ("hw01-login-wrong", "hw01-login-wrong-204")

    def password(requirement_id: str) -> str:
        body = requirements[requirement_id].http_probes[0].body
        assert body is not None
        value = json.loads(body)["password"]
        assert isinstance(value, str)
        return value

    sample = password("hw01-create")
    assert sample == "Probe9!Alpha"
    assert all(password(requirement_id) == sample for requirement_id in positive)
    assert any(char in string.ascii_uppercase for char in sample)
    assert any(char in string.ascii_lowercase for char in sample)
    assert any(char in string.digits for char in sample)
    assert any(char in string.punctuation for char in sample)
    assert all(password(requirement_id) != sample for requirement_id in wrong)
    create = requirements["hw01-create"].http_probes[0]
    assert create.expected_status == 201
    assert create.allowed_statuses == (201, 200)
    assert 400 not in create.allowed_statuses
    assert {r.id: r.depends_on for r in hw01.requirements if r.depends_on} == {
        "hw01-read-back": ("hw01-create",),
        "hw01-login": ("hw01-create",),
        "hw01-login-returns-user": ("hw01-create", "hw01-login"),
        "hw01-update": ("hw01-create",),
        "hw01-update-read-back": ("hw01-update", "hw01-read-back"),
    }


def test_committed_requirements_validate_against_the_builtin_checks() -> None:
    assert catalog().validate_all() == ()


def test_round_for_hw01_selects_general_and_hw01() -> None:
    assert [a.id for a in catalog().select_for_round("hw01")] == ["_general", "hw01"]


def test_the_created_user_id_is_captured_before_it_is_used() -> None:
    _, hw01, *_ = catalog().list_assignments()
    probes = [p for r in hw01.requirements for p in r.http_probes]

    (capturing,) = [p for p in probes if p.capture is not None]
    assert capturing.requirement_id == "hw01-create"
    assert capturing.capture is not None
    assert (capturing.capture.name, capturing.capture.source) == ("user_id", "body:id")
    users = [p.requirement_id for p in probes if "user_id" in p.variables_used]
    assert users == [
        "hw01-read-back",
        "hw01-login-returns-user",
        "hw01-update",
        "hw01-update-read-back",
    ]


def test_hw02_and_hw03_are_major_notes_only_and_need_the_layer_projects() -> None:
    _, _, hw02, hw03 = catalog().list_assignments()

    assert hw02.short_name == "HW2" and hw03.short_name == "HW3"
    assert [r.id for r in hw02.requirements] == [
        "hw02-layer-projects",
        "hw02-references",
        "hw02-file-access-in-repositories",
    ]
    assert [r.id for r in hw03.requirements] == [
        "hw03-layer-projects",
        "hw03-references",
        "hw03-service-interfaces",
        "hw03-service-interfaces-taken",
        "hw03-repository-interfaces",
        "hw03-repository-interfaces-taken",
        "hw03-registered",
        "hw03-no-new-dependencies",
    ]
    for assignment in (hw02, hw03):
        first, *rest = assignment.requirements
        assert all(r.severity is Severity.MAJOR for r in assignment.requirements)
        assert all(r.feedback is Feedback.NOTES_ONLY for r in assignment.requirements)
        assert first.depends_on == ()
        assert all(r.depends_on == (first.id,) for r in rest)


def test_hw02_skips_and_hw03_applies_on_the_same_di_condition_and_roles() -> None:
    _, _, hw02, hw03 = catalog().list_assignments()

    assert hw02.skip_when is not None and hw03.applies_when is not None
    assert hw02.applies_when is None and hw03.skip_when is None
    assert hw02.skip_when == hw03.applies_when
    assert dict(hw02.skip_when.params) == {"min_registrations": 0, "min_interface_constructors": 1}
    roles = [r.params["roles"] for a in (hw02, hw03) for r in a.requirements]
    assert len({json.dumps(_plain(role), sort_keys=True) for role in roles}) == 1


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value
