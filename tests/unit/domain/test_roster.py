import pytest
from pydantic import ValidationError

from hw_checker.domain import Pair, RosterRow, RosterWarning, RosterWarningKind, Student
from tests.unit.domain import builders

PARTNER = Student(name="Noa Cohen", github_url="https://github.com/noa/api")


def test_student_builds() -> None:
    assert builders.student().name == "Dana Levi"


@pytest.mark.parametrize("field", ["name", "github_url"])
def test_student_rejects_blank_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        builders.student(**{field: "  "})


def test_models_are_frozen() -> None:
    student = builders.student()

    with pytest.raises(ValidationError):
        student.name = "Other"  # type: ignore[misc]


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        builders.student(nickname="D")


@pytest.mark.parametrize("members", [(builders.student(),), (builders.student(), PARTNER)])
def test_pair_accepts_one_or_two_members(members: tuple[Student, ...]) -> None:
    assert Pair(pair_id="pair-1", members=members).members == members


@pytest.mark.parametrize(
    "members",
    [(), (builders.student(), PARTNER, builders.student(github_url="https://github.com/x/y"))],
)
def test_pair_rejects_zero_or_three_members(members: tuple[Student, ...]) -> None:
    with pytest.raises(ValidationError):
        Pair(pair_id="pair-1", members=members)


def test_pair_rejects_duplicate_members_by_github_url() -> None:
    same_url = builders.student(name="Dana L.")

    with pytest.raises(ValidationError, match="distinct github_url"):
        Pair(pair_id="pair-1", members=(builders.student(), same_url))


def test_pair_rejects_padded_id() -> None:
    with pytest.raises(ValidationError):
        Pair(pair_id=" pair-1", members=(builders.student(),))


def test_repo_target_builds() -> None:
    assert builders.repo_target().repo_id == "dana-api"


def test_repo_target_rejects_empty_repo_id() -> None:
    with pytest.raises(ValidationError):
        builders.repo_target(repo_id="")


def test_roster_row_builds_without_partner() -> None:
    row = RosterRow(name="Dana Levi", github_url="not a url", row_number=2)

    assert row.partner_name is None


@pytest.mark.parametrize("row_number", [0, -1])
def test_roster_row_rejects_row_number_below_one(row_number: int) -> None:
    with pytest.raises(ValidationError):
        RosterRow(name="Dana Levi", github_url="x", row_number=row_number)


def test_roster_row_rejects_blank_partner_name() -> None:
    with pytest.raises(ValidationError):
        RosterRow(name="Dana Levi", partner_name=" ", github_url="x", row_number=2)


def test_roster_warning_builds() -> None:
    warning = RosterWarning(kind=RosterWarningKind.MISSING_PARTNER, message="No partner", row=3)

    assert warning.kind is RosterWarningKind.MISSING_PARTNER


def test_roster_warning_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        RosterWarning.model_validate({"kind": "other", "message": "x"})


def test_roster_warning_rejects_row_below_one() -> None:
    with pytest.raises(ValidationError):
        RosterWarning(kind=RosterWarningKind.INVALID_URL, message="Bad URL", row=0)
