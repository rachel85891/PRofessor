import pytest
from pydantic import ValidationError

from hw_checker.domain import Assignment, RequirementKind, Severity
from tests.unit.domain import builders


def test_severity_is_ordered_minor_major_critical() -> None:
    assert Severity.MINOR < Severity.MAJOR < Severity.CRITICAL
    assert Severity.CRITICAL > Severity.MINOR
    assert Severity.MAJOR <= Severity.MAJOR
    assert Severity.MAJOR >= Severity.MINOR
    assert max(Severity) is Severity.CRITICAL
    assert sorted([Severity.CRITICAL, Severity.MINOR, Severity.MAJOR]) == [
        Severity.MINOR,
        Severity.MAJOR,
        Severity.CRITICAL,
    ]


def test_severity_does_not_compare_with_plain_strings() -> None:
    with pytest.raises(TypeError):
        _ = Severity.MINOR < "zzz"


def test_requirement_kind_has_all_five_kinds() -> None:
    assert {kind.value for kind in RequirementKind} == {
        "structure",
        "code",
        "http",
        "build",
        "custom",
    }


def test_probe_defaults_allowed_statuses_to_expected() -> None:
    assert builders.probe().allowed_statuses == (200,)


def test_probe_accepts_expected_among_allowed() -> None:
    probe = builders.probe(expected_status=404, allowed_statuses=(404, 204))

    assert probe.allowed_statuses == (404, 204)


def test_probe_rejects_expected_not_in_allowed() -> None:
    with pytest.raises(ValidationError, match="must include expected_status"):
        builders.probe(expected_status=200, allowed_statuses=(201,))


@pytest.mark.parametrize("status", [99, 600])
def test_probe_rejects_expected_status_out_of_range(status: int) -> None:
    with pytest.raises(ValidationError):
        builders.probe(expected_status=status)


def test_probe_rejects_allowed_status_out_of_range() -> None:
    with pytest.raises(ValidationError):
        builders.probe(allowed_statuses=(200, 700))


def test_probe_rejects_path_without_leading_slash() -> None:
    with pytest.raises(ValidationError):
        builders.probe(path="api/products")


def test_probe_candidate_paths_are_the_path_then_the_alternatives() -> None:
    probe = builders.probe(path="/api/users", path_alternatives=("/api/user", "/api/people"))

    assert probe.candidate_paths == ("/api/users", "/api/user", "/api/people")
    assert builders.probe(path="/api/users").candidate_paths == ("/api/users",)


def test_probe_rejects_alternative_without_leading_slash() -> None:
    with pytest.raises(ValidationError, match="path_alternatives"):
        builders.probe(path_alternatives=("/api/user", "api/users"))


def test_probe_rejects_unknown_method() -> None:
    with pytest.raises(ValidationError):
        builders.probe(method="FETCH")


def test_requirement_builds() -> None:
    assert builders.requirement().kind is RequirementKind.CODE


def test_http_requirement_with_probe_builds() -> None:
    requirement = builders.requirement(kind=RequirementKind.HTTP, http_probes=(builders.probe(),))

    assert len(requirement.http_probes) == 1


def test_http_requirement_without_probe_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one probe"):
        builders.requirement(kind=RequirementKind.HTTP)


def test_non_http_requirement_with_probe_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not have http probes"):
        builders.requirement(kind=RequirementKind.CODE, http_probes=(builders.probe(),))


def test_requirement_rejects_blank_allowed_alternative() -> None:
    with pytest.raises(ValidationError):
        builders.requirement(allowed_alternatives=(" ",))


def test_assignment_builds() -> None:
    assignment = Assignment(
        id="hw03",
        order=3,
        title="Products controller",
        requirements=(builders.requirement(), builders.requirement(id="hw03-r3")),
        lenient_notes=("Do not require async/await at this stage",),
    )

    assert len(assignment.requirements) == 2


def test_general_assignment_has_order_zero() -> None:
    assert Assignment(id="_general", order=0, title="General", requirements=()).order == 0


def test_assignment_rejects_negative_order() -> None:
    with pytest.raises(ValidationError):
        Assignment(id="hw01", order=-1, title="Intro", requirements=())


def test_assignment_rejects_duplicate_requirement_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate requirement ids: hw03-r2"):
        Assignment(
            id="hw03",
            order=3,
            title="Products controller",
            requirements=(builders.requirement(), builders.requirement()),
        )


SOURCE_HASH = "0" * 63 + "a"


@pytest.mark.parametrize("kind", [RequirementKind.STRUCTURE, RequirementKind.CUSTOM])
def test_structure_and_custom_requirements_need_a_check(kind: RequirementKind) -> None:
    with pytest.raises(ValidationError, match="needs a check"):
        builders.requirement(kind=kind)


@pytest.mark.parametrize("kind", [RequirementKind.STRUCTURE, RequirementKind.CUSTOM])
def test_structure_and_custom_requirements_with_check_and_params_build(
    kind: RequirementKind,
) -> None:
    requirement = builders.requirement(
        kind=kind, check="file_exists", params={"glob": "**/Controllers/*.cs", "min": 1}
    )

    assert requirement.check == "file_exists"
    assert dict(requirement.params) == {"glob": "**/Controllers/*.cs", "min": 1}


@pytest.mark.parametrize("kind", [RequirementKind.CODE, RequirementKind.BUILD])
def test_code_and_build_requirements_must_not_have_a_check(kind: RequirementKind) -> None:
    with pytest.raises(ValidationError, match="must not have a check"):
        builders.requirement(kind=kind, check="file_exists")


def test_http_requirement_must_not_have_a_check() -> None:
    with pytest.raises(ValidationError, match="must not have a check"):
        builders.requirement(
            kind=RequirementKind.HTTP, http_probes=(builders.probe(),), check="file_exists"
        )


def test_params_without_check_are_rejected() -> None:
    with pytest.raises(ValidationError, match="only allowed together with a check"):
        builders.requirement(params={"glob": "*.cs"})


def test_params_default_to_empty() -> None:
    assert dict(builders.requirement().params) == {}


def test_params_are_immutable() -> None:
    requirement = builders.requirement(
        kind=RequirementKind.STRUCTURE, check="file_exists", params={"glob": "*.cs"}
    )

    with pytest.raises(TypeError):
        requirement.params["glob"] = "other"  # type: ignore[index]


def test_params_keep_value_types_and_round_trip() -> None:
    requirement = builders.requirement(
        kind=RequirementKind.CUSTOM,
        check="naming",
        params={"flag": True, "count": 2, "ratio": 0.5, "names": ["Get", "Post"]},
    )

    assert requirement.params["flag"] is True
    assert requirement.params["names"] == ("Get", "Post")
    assert type(requirement).model_validate_json(requirement.model_dump_json()) == requirement


def test_params_reject_nested_mappings() -> None:
    with pytest.raises(ValidationError):
        builders.requirement(kind=RequirementKind.CUSTOM, check="x", params={"a": {"b": 1}})


def test_source_hash_is_optional_and_validated() -> None:
    plain = Assignment(id="hw01", order=1, title="Intro", requirements=())
    hashed = Assignment(id="hw01", order=1, title="Intro", requirements=(), source_hash=SOURCE_HASH)

    assert plain.source_hash is None
    assert hashed.source_hash == SOURCE_HASH
    with pytest.raises(ValidationError):
        Assignment(id="hw01", order=1, title="Intro", requirements=(), source_hash="abc")


def test_probe_carries_its_requirement_and_assignment() -> None:
    probe = builders.probe()

    assert (probe.requirement_id, probe.assignment_id) == ("hw03-r2", "hw03")


def test_requirement_rejects_probe_of_another_requirement() -> None:
    with pytest.raises(ValidationError, match="requirement_id 'hw03-r2'"):
        builders.requirement(
            kind=RequirementKind.HTTP, http_probes=(builders.probe(requirement_id="hw03-r9"),)
        )


def test_assignment_rejects_probe_of_another_assignment() -> None:
    requirement = builders.requirement(
        kind=RequirementKind.HTTP, http_probes=(builders.probe(assignment_id="hw04"),)
    )

    with pytest.raises(ValidationError, match="assignment_id 'hw03'"):
        Assignment(id="hw03", order=3, title="Products", requirements=(requirement,))
