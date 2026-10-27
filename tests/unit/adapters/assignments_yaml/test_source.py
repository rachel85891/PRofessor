import hashlib
import logging
from pathlib import Path

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.domain import RequirementKind, Severity
from hw_checker.domain.errors import InvalidAssignmentError
from hw_checker.ports import AssignmentSource
from tests.fakes.assignments import FakeAssignmentSource

GENERAL = """\
id: _general
order: 0
title: General requirements
requirements:
  - id: gen-r1
    kind: code
    title: Reasonable HTTP status codes
    severity: minor
"""

HW01 = """\
id: hw01
order: 1
title: Products controller
taught_techniques: [controllers, routing attributes]
lenient_notes:
  - Do not require async/await at this stage
requirements:
  - id: hw01-r1
    kind: structure
    title: ProductsController exists
    severity: major
    check: file_exists
    params:
      glob: "**/Controllers/ProductsController.cs"
  - id: hw01-r2
    kind: http
    title: POST /api/products creates a product
    severity: critical
    allowed_alternatives: ["Returning 200 instead of 201 is acceptable"]
    probes:
      - method: POST
        path: /api/products
        body:
          name: Pen
          price: 3.5
        expected_status: 201
        allowed_statuses: [201, 200]
        notes: Body must be JSON
      - method: GET
        path: /api/products
        expected_status: 200
  - id: hw01-r3
    kind: build
    title: The project builds
    severity: critical
"""

HW02 = """\
id: hw02
order: 2
title: Get by id
requirements:
  - id: hw02-r1
    kind: code
    title: GET by id returns NotFound when missing
    severity: major
    description: The action should not return 200 with null.
"""


def write_tree(root: Path, files: dict[str, str | bytes]) -> Path:
    directory = root / "assignments"
    directory.mkdir(exist_ok=True)
    for folder, content in files.items():
        (directory / folder).mkdir(parents=True, exist_ok=True)
        path = directory / folder / "requirements.yaml"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return directory


def load(root: Path, files: dict[str, str | bytes]) -> YamlAssignmentSource:
    return YamlAssignmentSource(write_tree(root, files))


def problems_of(source: YamlAssignmentSource) -> str:
    with pytest.raises(InvalidAssignmentError) as raised:
        source.load_all()
    return str(raised.value)


def test_source_satisfies_the_port(tmp_path: Path) -> None:
    port: AssignmentSource = YamlAssignmentSource(tmp_path)
    fake: AssignmentSource = FakeAssignmentSource()

    assert port is not None
    assert fake.load_all() == ()


def test_valid_tree_loads_sorted_with_hashes(tmp_path: Path) -> None:
    source = load(tmp_path, {"hw02": HW02, "_general": GENERAL, "hw01": HW01})

    general, hw01, hw02 = source.load_all()

    assert [general.id, hw01.id, hw02.id] == ["_general", "hw01", "hw02"]
    directory = tmp_path / "assignments"
    for assignment in (general, hw01, hw02):
        raw = (directory / assignment.id / "requirements.yaml").read_bytes()
        assert assignment.source_hash == hashlib.sha256(raw).hexdigest()
    assert hw01.taught_techniques == ("controllers", "routing attributes")
    assert hw01.lenient_notes == ("Do not require async/await at this stage",)


def test_requirement_fields_are_mapped(tmp_path: Path) -> None:
    _, hw01, _ = load(tmp_path, {"_general": GENERAL, "hw01": HW01, "hw02": HW02}).load_all()

    structure, http, build = hw01.requirements
    assert (structure.kind, structure.check) == (RequirementKind.STRUCTURE, "file_exists")
    assert dict(structure.params) == {"glob": "**/Controllers/ProductsController.cs"}
    assert http.severity is Severity.CRITICAL
    assert http.allowed_alternatives == ("Returning 200 instead of 201 is acceptable",)
    post, get = http.http_probes
    assert (post.requirement_id, post.assignment_id) == ("hw01-r2", "hw01")
    assert post.allowed_statuses == (201, 200)
    assert post.notes == "Body must be JSON"
    assert get.allowed_statuses == (200,)
    assert get.body is None
    assert build.kind is RequirementKind.BUILD


def test_path_alternatives_are_optional_and_loaded_in_order(tmp_path: Path) -> None:
    hw = HW01.replace(
        "      - method: GET\n        path: /api/products\n",
        "      - method: GET\n        path: /api/products\n"
        "        path_alternatives: [/api/product, /api/Products/all]\n",
    )

    _, hw01 = load(tmp_path, {"_general": GENERAL, "hw01": hw}).load_all()

    post, get = hw01.requirements[1].http_probes
    assert post.path_alternatives == ()
    assert get.candidate_paths == ("/api/products", "/api/product", "/api/Products/all")


def test_path_alternative_without_leading_slash_is_reported(tmp_path: Path) -> None:
    hw = HW01.replace(
        "      - method: GET\n        path: /api/products\n",
        "      - method: GET\n        path: /api/products\n"
        "        path_alternatives: [api/product]\n",
    )

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": hw}))

    assert "requirement 'hw01-r2'" in message
    assert "path_alternatives" in message


CHAINED = """\
id: hw02
order: 2
title: Users
requirements:
  - id: hw02-r1
    kind: http
    title: Create a user
    severity: critical
    probes:
      - method: POST
        path: /api/users
        body: {userName: newuser}
        expected_status: 201
        capture: {name: userId, from: body:id}
  - id: hw02-r2
    kind: http
    title: Read it back
    severity: major
    probes:
      - method: GET
        path: /api/users/{userId}
        expected_status: 200
"""


def test_capture_loads_and_later_probes_may_use_it(tmp_path: Path) -> None:
    _, hw02 = load(tmp_path, {"_general": GENERAL, "hw02": CHAINED}).load_all()

    create, read = (requirement.http_probes[0] for requirement in hw02.requirements)
    assert create.capture is not None
    assert (create.capture.name, create.capture.source) == ("userId", "body:id")
    assert read.variables_used == ("userId",)


def test_variable_used_before_it_is_captured_names_the_requirement(tmp_path: Path) -> None:
    swapped = CHAINED.replace("path: /api/users/{userId}", "path: /api/users/{orderId}")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": swapped}))

    assert "requirement 'hw02-r2', probe 1: {orderId} is used before any probe captures it" in (
        message
    )


def test_content_expectations_load(tmp_path: Path) -> None:
    expecting = CHAINED.replace(
        "        path: /api/users/{userId}\n        expected_status: 200\n",
        "        path: /api/users/{userId}\n        expected_status: 200\n"
        "        expect_json: object\n        expect_values: [newuser, '{userId}']\n",
    )

    _, hw02 = load(tmp_path, {"_general": GENERAL, "hw02": expecting}).load_all()

    read = hw02.requirements[1].http_probes[0]
    assert read.expect_json is not None and read.expect_json.value == "object"
    assert read.expect_values == ("newuser", "{userId}")


def test_unknown_expected_shape_is_reported(tmp_path: Path) -> None:
    broken = CHAINED.replace(
        "        capture: {name: userId, from: body:id}\n",
        "        capture: {name: userId, from: body:id}\n        expect_json: array\n",
    )

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": broken}))

    assert "requirement 'hw02-r1'" in message and "expect_json" in message


def test_expected_value_variable_used_before_capture_is_reported(tmp_path: Path) -> None:
    early = CHAINED.replace(
        "        capture: {name: userId, from: body:id}\n",
        "        capture: {name: userId, from: body:id}\n        expect_values: ['{email}']\n",
    )

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": early}))

    assert "requirement 'hw02-r1', probe 1: {email} is used before any probe captures it" in (
        message
    )


def test_invalid_capture_source_is_reported(tmp_path: Path) -> None:
    broken = CHAINED.replace("from: body:id", "from: header:Location")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": broken}))

    assert "requirement 'hw02-r1'" in message and "capture" in message


def test_probe_body_mapping_becomes_compact_json(tmp_path: Path) -> None:
    _, hw01, _ = load(tmp_path, {"_general": GENERAL, "hw01": HW01, "hw02": HW02}).load_all()

    assert hw01.requirements[1].http_probes[0].body == '{"name":"Pen","price":3.5}'


def test_probe_body_text_is_kept_as_written(tmp_path: Path) -> None:
    hw = HW02.replace(
        "    kind: code\n    title: GET by id returns NotFound when missing\n",
        "    kind: http\n    title: Create\n    probes:\n      - method: POST\n"
        '        path: /api/items\n        body: \'{"name": "שולחן"}\'\n'
        "        expected_status: 201\n",
    )

    _, hw02 = load(tmp_path, {"_general": GENERAL, "hw02": hw}).load_all()

    assert hw02.requirements[0].http_probes[0].body == '{"name": "שולחן"}'


def test_only_general_is_valid(tmp_path: Path) -> None:
    (general,) = load(tmp_path, {"_general": GENERAL}).load_all()

    assert general.id == "_general"


def test_new_assignment_folder_is_picked_up_without_code_changes(tmp_path: Path) -> None:
    source = load(tmp_path, {"_general": GENERAL, "hw01": HW01})
    assert [assignment.id for assignment in source.load_all()] == ["_general", "hw01"]

    write_tree(tmp_path, {"hw02": HW02})

    assert [assignment.id for assignment in source.load_all()] == ["_general", "hw01", "hw02"]


def test_hidden_folders_and_folders_without_file_are_ignored_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = write_tree(tmp_path, {"_general": GENERAL, ".draft": HW01})
    (directory / "hw02").mkdir()
    (directory / "hw02" / "requirements.yml").write_text(HW02, encoding="utf-8")
    (directory / "README.md").write_text("notes", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="hw_checker.adapters.assignments_yaml.source"):
        assignments = YamlAssignmentSource(directory).load_all()

    assert [assignment.id for assignment in assignments] == ["_general"]
    ignored = [record.getMessage() for record in caplog.records if "Ignored" in record.getMessage()]
    assert len(ignored) == 1
    assert ".draft" in ignored[0]
    assert "hw02" in ignored[0]


def test_unknown_key_names_the_key_file_and_requirement(tmp_path: Path) -> None:
    hw = HW02.replace("    severity: major\n", "    severity: major\n    chek: file_exists\n")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": hw}))

    assert "assignments/hw02/requirements.yaml: requirement 'hw02-r1', field 'chek'" in message
    assert "unknown key" in message


def test_unknown_key_in_a_probe_names_the_probe(tmp_path: Path) -> None:
    hw = HW01.replace("        notes: Body must be JSON\n", "        note: Body must be JSON\n")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": hw}))

    assert "requirement 'hw01-r2', probe 1, field 'note': unknown key" in message


def test_unknown_top_level_key(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"_general": GENERAL + "titel: x\n"}))

    assert "assignments/_general/requirements.yaml, field 'titel': unknown key" in message


def test_missing_required_field(tmp_path: Path) -> None:
    hw = HW02.replace("    severity: major\n", "")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": hw}))

    assert "requirement 'hw02-r1', field 'severity': this required field is missing" in message


def test_folder_and_id_mismatch(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw2": HW02}))

    assert "assignments/hw2/requirements.yaml: id 'hw02' does not match the folder name 'hw2'" in (
        message
    )


def test_duplicate_order(tmp_path: Path) -> None:
    hw = HW02.replace("order: 2", "order: 1")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": HW01, "hw02": hw}))

    assert "hw01/requirements.yaml and assignments/hw02/requirements.yaml use the same order 1" in (
        message
    )


def test_duplicate_requirement_id_across_files(tmp_path: Path) -> None:
    hw = HW02.replace("id: hw02-r1", "id: hw01-r1")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": HW01, "hw02": hw}))

    assert "use the same requirement id 'hw01-r1'" in message


def test_missing_general(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"hw01": HW01}))

    assert "_general is missing" in message


def test_general_with_non_zero_order(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"_general": GENERAL.replace("order: 0", "order: 5")}))

    assert "_general must have order: 0 (found 5)" in message


def test_other_assignment_with_order_zero(tmp_path: Path) -> None:
    hw = HW02.replace("order: 2", "order: 0")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": hw}))

    assert "hw02/requirements.yaml: order must be 1 or more" in message


def test_malformed_yaml_names_file_and_line(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"_general": "id: _general\norder: [0\n"}))

    assert "assignments/_general/requirements.yaml: not valid YAML (line" in message


def test_non_utf8_file(tmp_path: Path) -> None:
    content = GENERAL.replace("General requirements", "דרישות").encode("cp1255")

    message = problems_of(load(tmp_path, {"_general": content}))

    assert "assignments/_general/requirements.yaml: the file is not UTF-8 text" in message


def test_file_that_is_not_a_mapping(tmp_path: Path) -> None:
    message = problems_of(load(tmp_path, {"_general": "- just\n- a list\n"}))

    assert "must contain keys such as id, order and title" in message


def test_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(InvalidAssignmentError, match="Assignments folder not found"):
        YamlAssignmentSource(tmp_path / "absent").load_all()


def test_domain_rule_violation_names_file_and_requirement(tmp_path: Path) -> None:
    hw = HW02.replace("kind: code", "kind: http")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw02": hw}))

    assert (
        "assignments/hw02/requirements.yaml: requirement 'hw02-r1': "
        "an http requirement needs at least one probe"
    ) in message


def test_domain_field_error_names_the_file_key(tmp_path: Path) -> None:
    hw = HW01.replace("expected_status: 201", "expected_status: 700")

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": hw}))

    assert "requirement 'hw01-r2', probe 1, field 'expected_status'" in message


def test_several_problems_are_reported_together(tmp_path: Path) -> None:
    broken_hw01 = HW01.replace("severity: major", "severity: huge").replace(
        "kind: build", "kind: build\n    check: compiles"
    )
    broken_hw02 = HW02.replace("order: 2", "order: 1").replace("title: Get by id", "titel: x")

    message = problems_of(
        load(
            tmp_path,
            {
                "_general": GENERAL.replace("order: 0", "order: 3"),
                "hw01": broken_hw01,
                "hw02": broken_hw02,
            },
        )
    )

    assert "requirement 'hw01-r1', field 'severity'" in message
    assert "requirement 'hw01-r3': a build requirement must not have a check" in message
    assert "hw02/requirements.yaml, field 'titel': unknown key" in message
    assert "_general must have order: 0 (found 3)" in message
    assert message.count("\n- ") >= 4


def test_problems_in_different_requirements_of_one_file_are_all_reported(tmp_path: Path) -> None:
    hw = HW01.replace("    check: file_exists\n", "    chek: file_exists\n").replace(
        "        path: /api/products\n        body:", "        path: api/products\n        body:"
    )

    message = problems_of(load(tmp_path, {"_general": GENERAL, "hw01": hw}))

    assert "requirement 'hw01-r1', field 'chek': unknown key" in message
    assert "requirement 'hw01-r2', probe 1, field 'path'" in message
