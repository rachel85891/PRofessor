from datetime import UTC, datetime, timedelta, timezone

import pytest

from hw_checker.adapters.git_local.log_format import LOG_FORMAT, parse_log_output

SHA_A = "a" * 40
SHA_B = "b" * 40
# Hebrew letters that resemble Latin ones are intended here.
HEBREW_AUTHOR = "דנה לוי"
HEBREW_MESSAGE = "תרגיל 3\n\nהוספתי בקר מוצרים"  # noqa: RUF001


def record(sha: str, author: str, date: str, message: str) -> str:
    return f"{sha}\x1f{author}\x1f{date}\x1f{message}"


def test_format_uses_separators_and_strict_iso_date() -> None:
    assert LOG_FORMAT == "%H%x1f%an%x1f%aI%x1f%B"


def test_parses_records_newest_first_with_multiline_and_hebrew_messages() -> None:
    output = (
        record(SHA_A, HEBREW_AUTHOR, "2026-10-20T21:30:00+03:00", HEBREW_MESSAGE + "\n")
        + "\0"
        + "\n"
        + record(SHA_B, "Noa Cohen", "2026-10-19T08:00:00Z", "Initial commit\n")
        + "\0"
    )

    newest, oldest = parse_log_output(output)

    assert newest.sha == SHA_A
    assert newest.author_name == HEBREW_AUTHOR
    assert newest.message == HEBREW_MESSAGE
    israel = timezone(timedelta(hours=3))
    assert newest.authored_at == datetime(2026, 10, 20, 21, 30, tzinfo=israel)
    assert oldest.message == "Initial commit"
    assert oldest.authored_at == datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


def test_message_may_contain_the_field_separator() -> None:
    (commit,) = parse_log_output(record(SHA_A, "A", "2026-10-19T08:00:00Z", "a\x1fb\n") + "\0")

    assert commit.message == "a\x1fb"


def test_empty_message_and_author_are_allowed() -> None:
    (commit,) = parse_log_output(record(SHA_A, "", "2026-10-19T08:00:00Z", "") + "\0")

    assert (commit.author_name, commit.message) == ("", "")


def test_empty_output_gives_no_commits() -> None:
    assert parse_log_output("") == ()


@pytest.mark.parametrize(
    "output",
    [
        f"{SHA_A}\x1fonly two fields\0",
        record(SHA_A, "A", "not a date", "msg") + "\0",
        record(SHA_A, "A", "2026-10-19T08:00:00", "naive date") + "\0",
    ],
)
def test_malformed_records_raise_value_error(output: str) -> None:
    with pytest.raises(ValueError):
        parse_log_output(output)
