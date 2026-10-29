import logging
import random
from pathlib import Path

import pytest

from hw_checker.domain import RosterResult, RosterRow, RosterWarningKind, parse_github_url
from hw_checker.domain.errors import RosterError
from hw_checker.ports import RosterReader
from hw_checker.services.roster import RosterService
from tests.fakes.roster import FakeRosterReader

SOURCE = Path("roster.xlsx")


def row(number: int, name: str, url: str, partner: str | None = None) -> RosterRow:
    return RosterRow(row_number=number, name=name, partner_name=partner, github_url=url)


def gh(owner: str, name: str = "api") -> str:
    return f"https://github.com/{owner}/{name}"


def load(*rows: RosterRow) -> RosterResult:
    return RosterService(FakeRosterReader(rows)).load(SOURCE)


def kinds(result: RosterResult) -> list[tuple[int | None, RosterWarningKind]]:
    return [(warning.row, warning.kind) for warning in result.warnings]


def pair_members(result: RosterResult) -> list[list[str]]:
    return [[member.name for member in pair.members] for pair in result.pairs]


def test_fake_satisfies_the_port() -> None:
    reader: RosterReader = FakeRosterReader()

    assert reader.read_rows(SOURCE) == ()


def test_service_reads_the_given_source() -> None:
    reader = FakeRosterReader()

    RosterService(reader).load(SOURCE)

    assert reader.sources == [SOURCE]


def test_rule_1_invalid_url_is_skipped_with_warning() -> None:
    result = load(row(2, "Dana", "https://gitlab.com/dana/api"), row(3, "Noa", ""))

    assert result.repos == ()
    assert kinds(result) == [(2, RosterWarningKind.INVALID_URL), (3, RosterWarningKind.INVALID_URL)]
    assert "Row 2" in result.warnings[0].message


def test_rule_2_duplicate_repo_keeps_first_row() -> None:
    result = load(
        row(2, "Dana", gh("dana")),
        row(3, "Noa", "https://GitHub.com/Dana/API.git"),
        row(4, "Avi", gh("dana") + "/tree/main"),
    )

    assert [repo.student.name for repo in result.repos] == ["Dana"]
    assert kinds(result) == [
        (3, RosterWarningKind.DUPLICATE_REPO),
        (4, RosterWarningKind.DUPLICATE_REPO),
    ]
    assert "row 2" in result.warnings[0].message


def test_rule_3_mutual_partners_form_a_pair() -> None:
    result = load(row(2, "Dana", gh("dana"), "Noa"), row(3, "Noa", gh("noa"), "Dana"))

    assert pair_members(result) == [["Dana", "Noa"]]
    assert result.warnings == ()
    assert {repo.pair_id for repo in result.repos} == {result.pairs[0].pair_id}


def test_rule_4_one_sided_partner_without_own_partner_is_paired_with_warning() -> None:
    result = load(row(2, "Dana", gh("dana"), "Noa"), row(3, "Noa", gh("noa")))

    assert pair_members(result) == [["Dana", "Noa"]]
    assert kinds(result) == [(2, RosterWarningKind.ASYMMETRIC_DUPLICATE)]


def test_rule_4_applies_when_the_partnerless_row_comes_first() -> None:
    result = load(row(2, "Noa", gh("noa")), row(3, "Dana", gh("dana"), "Noa"))

    assert pair_members(result) == [["Noa", "Dana"]]
    assert kinds(result) == [(3, RosterWarningKind.ASYMMETRIC_DUPLICATE)]


def test_rule_5_partner_names_someone_else() -> None:
    result = load(
        row(2, "Dana", gh("dana"), "Noa"),
        row(3, "Noa", gh("noa"), "Avi"),
        row(4, "Avi", gh("avi"), "Noa"),
    )

    assert pair_members(result) == [["Dana"], ["Noa", "Avi"]]
    assert kinds(result) == [(2, RosterWarningKind.ASYMMETRIC_DUPLICATE)]
    assert "paired with Avi" in result.warnings[0].message


def test_rule_5_partner_names_someone_without_a_row() -> None:
    result = load(row(2, "Dana", gh("dana"), "Noa"), row(3, "Noa", gh("noa"), "Gil"))

    assert pair_members(result) == [["Dana"], ["Noa"]]
    assert kinds(result) == [
        (2, RosterWarningKind.ASYMMETRIC_DUPLICATE),
        (3, RosterWarningKind.MISSING_PARTNER),
    ]


def test_partnerless_student_claimed_twice_is_paired_with_the_first_row_only() -> None:
    result = load(
        row(2, "Dana", gh("dana"), "Noa"),
        row(3, "Avi", gh("avi"), "Noa"),
        row(4, "Noa", gh("noa")),
    )

    assert pair_members(result) == [["Dana", "Noa"], ["Avi"]]
    assert kinds(result) == [
        (2, RosterWarningKind.ASYMMETRIC_DUPLICATE),
        (3, RosterWarningKind.ASYMMETRIC_DUPLICATE),
    ]


def test_rule_6_missing_partner_row() -> None:
    result = load(row(2, "Dana", gh("dana"), "Noa"))

    assert pair_members(result) == [["Dana"]]
    assert kinds(result) == [(2, RosterWarningKind.MISSING_PARTNER)]
    assert "'Noa'" in result.warnings[0].message


def test_rule_6_partner_whose_row_has_an_invalid_url_counts_as_missing() -> None:
    result = load(row(2, "Dana", gh("dana"), "Noa"), row(3, "Noa", "not a url", "Dana"))

    assert pair_members(result) == [["Dana"]]
    assert kinds(result) == [
        (2, RosterWarningKind.MISSING_PARTNER),
        (3, RosterWarningKind.INVALID_URL),
    ]


def test_rule_7_ambiguous_partner_name() -> None:
    result = load(
        row(2, "Dana", gh("dana"), "Noa"),
        row(3, "Noa", gh("noa1")),
        row(4, "Noa", gh("noa2")),
    )

    assert pair_members(result) == [["Dana"], ["Noa"], ["Noa"]]
    assert kinds(result) == [(2, RosterWarningKind.AMBIGUOUS_PARTNER)]
    assert "rows 3, 4" in result.warnings[0].message


def test_rule_8_no_partner_is_a_pair_of_one_without_warning() -> None:
    result = load(row(2, "Dana", gh("dana")))

    assert pair_members(result) == [["Dana"]]
    assert result.warnings == ()


def test_naming_yourself_counts_as_missing_partner() -> None:
    result = load(row(2, "Dana", gh("dana"), "Dana"))

    assert kinds(result) == [(2, RosterWarningKind.MISSING_PARTNER)]


def test_hebrew_names_match_across_spacing_and_case() -> None:
    result = load(
        row(2, "  דנה   לוי ", gh("dana"), "נועה כהן"),
        row(3, "נועה  כהן", gh("noa"), "דנה לוי"),
        row(4, "Avi  COHEN", gh("avi"), "gil levi"),
        row(5, "Gil Levi", gh("gil"), "avi cohen"),
    )

    assert len(result.pairs) == 2
    assert all(len(pair.members) == 2 for pair in result.pairs)
    assert result.warnings == ()


def test_pair_id_is_built_from_sorted_repo_ids() -> None:
    forward = load(row(2, "Zed", gh("zed"), "Amy"), row(3, "Amy", gh("amy"), "Zed"))
    backward = load(row(2, "Amy", gh("amy"), "Zed"), row(3, "Zed", gh("zed"), "Amy"))

    assert forward.pairs[0].pair_id == "amy__api+zed__api"
    assert backward.pairs[0].pair_id == forward.pairs[0].pair_id


def test_repo_targets_use_canonical_values() -> None:
    result = load(row(2, "Dana", "git@github.com:Dana-L/HW-Api.git"))

    repo = result.repos[0]
    assert (repo.repo_id, repo.owner, repo.name) == ("dana-l__hw-api", "dana-l", "hw-api")
    assert repo.url == "https://github.com/dana-l/hw-api"
    assert repo.student.github_url == repo.url
    assert repo.pair_id == "dana-l__hw-api"


def test_output_is_ordered_by_first_row_number() -> None:
    result = load(
        row(2, "Avi", gh("avi"), "Gil"),
        row(3, "Dana", gh("dana")),
        row(4, "Gil", gh("gil"), "Avi"),
        row(5, "Noa", gh("noa")),
    )

    assert [repo.student.name for repo in result.repos] == ["Avi", "Gil", "Dana", "Noa"]
    assert pair_members(result) == [["Avi", "Gil"], ["Dana"], ["Noa"]]


def test_same_input_gives_the_same_result() -> None:
    rows = (
        row(2, "Dana", gh("dana"), "Noa"),
        row(3, "Noa", gh("noa"), "Avi"),
        row(4, "Avi", gh("avi"), "Noa"),
        row(5, "Gil", "bad"),
    )

    assert load(*rows) == load(*rows)


MIXED_ROSTER = (
    row(2, "Dana", gh("dana"), "Noa"),
    row(3, "Noa", gh("noa"), "Dana"),
    row(4, "Avi", gh("avi"), "Gil"),
    row(5, "Gil", gh("gil")),
    row(6, "Bad Url", "https://gitlab.com/x/y"),
    row(7, "Copy", gh("dana") + ".git"),
    row(8, "Lonely", gh("lonely"), "Nobody"),
    row(9, "Twin", gh("twin1")),
    row(10, "Twin", gh("twin2")),
    row(11, "Asker", gh("asker"), "Twin"),
    row(12, "Third", gh("third"), "Noa"),
    row(13, "Solo", gh("solo")),
    row(14, "Empty", ""),
    row(15, "Self", gh("self"), "Self"),
)


def assert_every_row_accounted_for(rows: tuple[RosterRow, ...], result: RosterResult) -> None:
    repo_urls = [repo.url for repo in result.repos]
    assert len(repo_urls) == len(set(repo_urls)), "a repo appears twice"
    warned_rows = {warning.row for warning in result.warnings}
    first_row_by_url: dict[str, int] = {}
    for roster_row in rows:
        parsed = parse_github_url(roster_row.github_url)
        if parsed is not None:
            first_row_by_url.setdefault(parsed.url, roster_row.row_number)
    rows_in_repos = {first_row_by_url[url] for url in repo_urls}
    assert len(rows_in_repos) == len(result.repos)
    for roster_row in rows:
        number = roster_row.row_number
        assert number in rows_in_repos or number in warned_rows, f"row {number} was dropped"
    assert sum(len(pair.members) for pair in result.pairs) == len(result.repos)


def test_no_row_is_dropped_silently_in_a_mixed_roster() -> None:
    result = load(*MIXED_ROSTER)

    assert_every_row_accounted_for(MIXED_ROSTER, result)
    assert len(result.repos) == 11  # 14 rows minus bad URL, duplicate and empty URL


@pytest.mark.parametrize("seed", range(25))
def test_no_row_is_dropped_silently_in_any_row_order(seed: int) -> None:
    rows = list(MIXED_ROSTER)
    random.Random(seed).shuffle(rows)

    assert_every_row_accounted_for(tuple(rows), load(*rows))


def test_reader_error_propagates_unchanged() -> None:
    error = RosterError("cannot read roster.xlsx")

    with pytest.raises(RosterError) as raised:
        RosterService(FakeRosterReader(error=error)).load(SOURCE)

    assert raised.value is error


def test_summary_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="hw_checker.services.roster"):
        load(row(2, "Dana", gh("dana")), row(3, "Bad", "x"))

    (record,) = caplog.records
    assert record.getMessage() == "Roster loaded: 2 rows, 1 repos, 1 pairs, 1 warnings"
    assert record.__dict__["warnings"] == 1


def three_repos() -> RosterResult:
    return load(
        row(2, "Avigail Test", gh("avigail")),
        row(3, "Batya Test", gh("batya")),
        row(4, "Chana Test", gh("chana")),
    )


def test_select_repos_keeps_every_repo_by_default() -> None:
    repos = three_repos().repos

    assert RosterService.select_repos(repos) == repos


def test_select_repos_keeps_only_the_given_ids_in_roster_order() -> None:
    repos = three_repos().repos

    selected = RosterService.select_repos(repos, repo_ids=("chana__api", "avigail__api"))

    assert [repo.repo_id for repo in selected] == ["avigail__api", "chana__api"]


def test_select_repos_takes_the_first_n_after_filtering() -> None:
    repos = three_repos().repos

    assert [r.repo_id for r in RosterService.select_repos(repos, limit=2)] == [
        "avigail__api",
        "batya__api",
    ]
    assert [
        r.repo_id
        for r in RosterService.select_repos(repos, repo_ids=("batya__api", "chana__api"), limit=1)
    ] == ["batya__api"]


def test_select_repos_rejects_an_id_that_is_not_in_the_roster() -> None:
    with pytest.raises(RosterError, match="Not in the roster: nobody__api"):
        RosterService.select_repos(three_repos().repos, repo_ids=("batya__api", "nobody__api"))


def test_select_repos_rejects_a_limit_below_one() -> None:
    with pytest.raises(RosterError, match="at least 1"):
        RosterService.select_repos(three_repos().repos, limit=0)
