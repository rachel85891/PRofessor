from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from hw_checker.checks.registry import CheckInput, CheckRegistry, check
from hw_checker.domain import Assignment, CheckParamDescription, Finding
from hw_checker.domain.errors import CheckRegistrationError, NotFoundError
from hw_checker.ports import CheckCatalog
from tests.fakes.checks import FakeCheckCatalog
from tests.unit.domain import builders


class FileExistsParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    glob: str
    min_count: int = 1
    ignore: tuple[str, ...] = ()
    case_sensitive: bool | None = None


class NoParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LooseParams(BaseModel):
    glob: str


@check("file_exists", FileExistsParams, "A file matching the pattern exists.")
def file_exists(check_input: CheckInput, params: FileExistsParams) -> tuple[Finding, ...]:
    return ()


@check("builds", NoParams, "The project builds.")
def builds(check_input: CheckInput, params: NoParams) -> tuple[Finding, ...]:
    return (builders.finding(),)


def unmarked(check_input: CheckInput, params: NoParams) -> tuple[Finding, ...]:
    return ()


def registry() -> CheckRegistry:
    return CheckRegistry([file_exists, builds])


def check_input() -> CheckInput:
    assignment = Assignment(id="hw03", order=3, title="Products", requirements=())
    return CheckInput(
        repo_dir=Path("repo"), assignment=assignment, requirement=builders.requirement()
    )


def test_decorator_returns_the_same_callable_function() -> None:
    def plain(check_input: CheckInput, params: NoParams) -> tuple[Finding, ...]:
        return ()

    marked = check("plain", NoParams, "Plain.")(plain)

    assert marked is plain
    assert builds(check_input(), NoParams()) == (builders.finding(),)


def test_registry_collects_checks_by_name() -> None:
    catalog = registry()

    assert catalog.check_names() == frozenset({"file_exists", "builds"})
    assert catalog.get("builds").function is builds
    assert catalog.get("file_exists").spec.params_model is FileExistsParams


def test_unknown_name_lookup_raises_not_found() -> None:
    with pytest.raises(NotFoundError, match="registered checks: builds, file_exists"):
        registry().get("missing")


def test_duplicate_names_are_rejected() -> None:
    @check("builds", NoParams, "Another check with the same name.")
    def other(check_input: CheckInput, params: NoParams) -> tuple[Finding, ...]:
        return ()

    with pytest.raises(CheckRegistrationError, match="two checks are named 'builds'"):
        CheckRegistry([builds, other])


def test_unmarked_function_is_rejected() -> None:
    with pytest.raises(CheckRegistrationError, match="unmarked is not marked with @check"):
        CheckRegistry([builds, unmarked])


def test_params_model_must_be_strict() -> None:
    @check("loose", LooseParams, "Loose params.")
    def loose(check_input: CheckInput, params: LooseParams) -> tuple[Finding, ...]:
        return ()

    with pytest.raises(CheckRegistrationError, match="must be frozen"):
        CheckRegistry([loose])


def test_valid_params_have_no_problems() -> None:
    assert registry().validate_params("file_exists", {"glob": "**/*.cs", "min_count": 2}) == ()
    assert registry().validate_params("builds", {}) == ()


def test_list_params_are_accepted_as_tuples() -> None:
    assert registry().validate_params("file_exists", {"glob": "*", "ignore": ("bin", "obj")}) == ()


def test_wrong_type_is_reported() -> None:
    (problem,) = registry().validate_params("file_exists", {"glob": "*", "min_count": "many"})

    assert problem.startswith("param 'min_count': ")


def test_missing_required_param_is_reported() -> None:
    assert registry().validate_params("file_exists", {}) == (
        "param 'glob': this required param is missing",
    )


def test_unknown_param_is_reported() -> None:
    assert registry().validate_params("builds", {"verbose": True}) == (
        "param 'verbose': unknown param; check the spelling",
    )


def test_unknown_check_is_a_problem_not_an_exception() -> None:
    assert registry().validate_params("missing", {}) == (
        "unknown check 'missing'; registered checks: builds, file_exists",
    )


def test_descriptions_list_fields_types_and_defaults() -> None:
    builds_description, file_exists_description = registry().describe()

    assert builds_description.name == "builds"
    assert builds_description.params == ()
    assert file_exists_description.description == "A file matching the pattern exists."
    assert file_exists_description.params == (
        CheckParamDescription(name="glob", type="text", required=True),
        CheckParamDescription(name="min_count", type="integer", required=False, default=1),
        CheckParamDescription(name="ignore", type="list of text", required=False, default=()),
        CheckParamDescription(name="case_sensitive", type="true/false", required=False),
    )


def test_registry_and_fake_satisfy_the_port() -> None:
    real: CheckCatalog = registry()
    fake: CheckCatalog = FakeCheckCatalog(["builds"])

    assert real.check_names() == frozenset({"file_exists", "builds"})
    assert fake.check_names() == frozenset({"builds"})


def test_registries_do_not_share_state() -> None:
    first = CheckRegistry([builds])
    second = CheckRegistry([file_exists])

    assert first.check_names() == frozenset({"builds"})
    assert second.check_names() == frozenset({"file_exists"})
    assert CheckRegistry([]).check_names() == frozenset()


def test_check_input_execution_is_optional() -> None:
    assert check_input().execution is None
