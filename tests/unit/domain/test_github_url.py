import pytest
from pydantic import ValidationError

from hw_checker.domain import RosterResult, RosterWarning, RosterWarningKind, parse_github_url
from tests.unit.domain import builders

CANONICAL = "https://github.com/dana-levi/hw-api"


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/dana-levi/hw-api",
        "http://github.com/dana-levi/hw-api",
        "https://www.github.com/dana-levi/hw-api",
        "https://GitHub.COM/dana-levi/hw-api",
        "https://github.com/dana-levi/hw-api/",
        "https://github.com/dana-levi/hw-api.git",
        "https://github.com/dana-levi/hw-api.git/",
        "https://github.com/dana-levi/hw-api/tree/main",
        "https://github.com/dana-levi/hw-api/blob/main/Program.cs",
        "https://github.com/dana-levi/hw-api?tab=readme-ov-file",
        "https://github.com/dana-levi/hw-api#readme",
        "git@github.com:dana-levi/hw-api.git",
        "git@github.com:dana-levi/hw-api",
        "  https://github.com/dana-levi/hw-api \n",
    ],
)
def test_accepted_forms_parse_to_the_same_repo(text: str) -> None:
    repo = parse_github_url(text)

    assert repo is not None
    assert repo.url == CANONICAL
    assert repo.owner == "dana-levi"
    assert repo.name == "hw-api"
    assert repo.repo_id == "dana-levi__hw-api"


@pytest.mark.parametrize(
    "path",
    [
        "/dana-levi/hw-api",
        "/dana-levi/hw-api/",
        "/dana-levi/hw-api.git",
        "/dana-levi/hw-api/tree/main",
        "/dana-levi/hw-api?tab=readme-ov-file",
        "/dana-levi/hw-api#readme",
    ],
)
@pytest.mark.parametrize("host", ["github.com", "www.github.com", "GitHub.COM", "WWW.GitHub.com"])
def test_links_without_a_scheme_match_their_https_form(host: str, path: str) -> None:
    schemeless = parse_github_url(f"  {host}{path} ")
    with_scheme = parse_github_url(f"https://{host}{path}")

    assert schemeless is not None
    assert schemeless == with_scheme
    assert schemeless.url == CANONICAL
    assert schemeless.repo_id == "dana-levi__hw-api"


def test_mixed_case_is_lowercased_in_url_and_repo_id() -> None:
    repo = parse_github_url("https://github.com/Dana-Levi/HW_Api.Net")

    assert repo is not None
    assert repo.url == "https://github.com/dana-levi/hw_api.net"
    assert repo.repo_id == "dana-levi__hw_api.net"


def test_repo_name_may_contain_dots_underscores_and_hyphens() -> None:
    repo = parse_github_url("https://github.com/a1/my.repo_name-2")

    assert repo is not None
    assert repo.repo_id == "a1__my.repo_name-2"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not a url",
        "https://gitlab.com/dana-levi/hw-api",
        "https://github.com.evil.com/dana-levi/hw-api",
        "https://gist.github.com/dana-levi/hw-api",
        "https://github.com/dana-levi",
        "https://github.com/dana-levi/",
        "https://github.com/",
        "https://github.com//hw-api",
        "ftp://github.com/dana-levi/hw-api",
        "gitlab.com/dana-levi/hw-api",
        "github.com/dana-levi",
        "github.com",
        "github.com.evil.com/dana-levi/hw-api",
        "https://user@github.com/dana-levi/hw-api",
        "https://github.com:8080/dana-levi/hw-api",
        "https://github.com/dana_levi/hw-api",
        "https://github.com/-dana/hw-api",
        "https://github.com/dana-/hw-api",
        "https://github.com/dana/hw api",
        "https://github.com/dana/hw$api",
        "https://github.com/dana/..",
        "https://github.com/dana/.git",
        "https://github.com/" + "a" * 40 + "/hw-api",
        "https://github.com/dana/" + "r" * 101,
        "git@github.com:dana-levi",
        "git@github.com:dana-levi/hw-api/extra",
        "git@gitlab.com:dana-levi/hw-api.git",
    ],
)
def test_invalid_forms_return_none(text: str) -> None:
    assert parse_github_url(text) is None


def test_hebrew_text_is_invalid() -> None:
    assert parse_github_url("https://github.com/דנה/api") is None


def test_roster_result_builds() -> None:
    warning = RosterWarning(kind=RosterWarningKind.DUPLICATE_REPO, message="Duplicate", row=4)

    result = RosterResult(repos=(builders.repo_target(),), pairs=(), warnings=(warning,))

    assert result.warnings[0].kind is RosterWarningKind.DUPLICATE_REPO


def test_roster_result_requires_all_fields() -> None:
    with pytest.raises(ValidationError):
        RosterResult.model_validate({"repos": ()})


def test_new_warning_kinds_exist() -> None:
    assert RosterWarningKind("duplicate_repo") is RosterWarningKind.DUPLICATE_REPO
    assert RosterWarningKind("ambiguous_partner") is RosterWarningKind.AMBIGUOUS_PARTNER
