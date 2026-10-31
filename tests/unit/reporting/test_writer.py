import csv
import io
from pathlib import Path

import pytest

from hw_checker.domain import FailureKind, NotEvaluatedReason
from hw_checker.domain.errors import StorageError
from hw_checker.reporting.wording import FAILURE_KINDS, NOT_EVALUATED_REASONS
from hw_checker.reporting.writer import (
    COUNTS_COLUMNS,
    MarkdownReportWriter,
    code_fence,
    table_cell,
)
from tests.unit.reporting import reports_data as data

GOLDEN = Path(__file__).parent / "golden" / "repo_report_full.md"


def reports_dir(runs_dir: Path) -> Path:
    return runs_dir / data.RUN_ID / "reports"


def test_full_repo_report_matches_the_golden_file(tmp_path: Path) -> None:
    path = MarkdownReportWriter(tmp_path).write_repo_report(data.full_report())

    assert path == reports_dir(tmp_path) / "noa__webapi.md"
    assert path.read_bytes() == GOLDEN.read_bytes()


def test_repo_with_nothing_to_report_says_so_in_one_line(tmp_path: Path) -> None:
    path = MarkdownReportWriter(tmp_path).write_repo_report(data.clean_report())

    text = path.read_text(encoding="utf-8")
    assert "Nothing to report: every checked requirement passed.\n" in text
    for heading in ("## Findings", "## Not evaluated", "## Errors", "Partner:"):
        assert heading not in text
    assert text.endswith("passed.\n")


def test_round_report_follows_roster_order(tmp_path: Path) -> None:
    files = MarkdownReportWriter(tmp_path).write_round_report(
        data.round_report(reports_dir(tmp_path))
    )

    text = files.round_report.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| ") and "webapi" in line]
    assert [row.split(" | ")[0] for row in rows] == [
        "| Tamar Testson",
        "| Noa Testman",
        "| Lea Testkin",
    ]
    assert "| failed | 1 | 1 | 1 | 2 | [report](noa__webapi.md) |" in rows[1]
    assert "| not saved | - | - | - | - | not written |" in rows[2]
    assert "Started: 2026-10-29 09:00 UTC" in text
    assert "## Roster warnings (1)" in text
    assert files.reports_dir == reports_dir(tmp_path)


def test_counts_file_has_a_bom_and_the_columns_in_roster_order(tmp_path: Path) -> None:
    files = MarkdownReportWriter(tmp_path).write_round_report(
        data.round_report(reports_dir(tmp_path))
    )

    raw = files.counts_file.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert tuple(rows[0]) == COUNTS_COLUMNS
    assert "grade" not in rows[0] and "score" not in rows[0]
    assert rows[1:] == [
        [
            "Tamar Testson",
            "",
            "https://github.com/tamar/webapi",
            "done",
            "0",
            "0",
            "0",
            "0",
            "tamar__webapi.md",
        ],
        [
            "Noa Testman",
            "Yael Testberg",
            "https://github.com/noa/webapi",
            "failed",
            "1",
            "1",
            "1",
            "2",
            "noa__webapi.md",
        ],
        ["Lea Testkin", "", "https://github.com/lea/webapi", "not saved", "", "", "", "", ""],
    ]


def test_counts_file_keeps_hebrew_names(tmp_path: Path) -> None:
    report = data.round_report(reports_dir(tmp_path))
    hebrew = data.repo("נועה בדיקה", owner="noa")
    report = report.model_copy(update={"repos": (hebrew,)})

    files = MarkdownReportWriter(tmp_path).write_round_report(report)

    assert "נועה בדיקה" in files.counts_file.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("reason", list(NotEvaluatedReason))
def test_every_not_evaluated_reason_has_a_sentence(reason: NotEvaluatedReason) -> None:
    sentence = NOT_EVALUATED_REASONS[reason]
    assert sentence[0].isupper() and sentence.endswith(".")


@pytest.mark.parametrize("kind", list(FailureKind))
def test_every_failure_kind_has_a_sentence(kind: FailureKind) -> None:
    sentence = FAILURE_KINDS[kind]
    assert sentence[0].isupper() and sentence.endswith(".")


def test_write_failure_is_a_storage_error(tmp_path: Path) -> None:
    blocker = tmp_path / "runs"
    blocker.write_text("a file where the folder should be", encoding="utf-8")

    with pytest.raises(StorageError, match=r"noa__webapi\.md"):
        MarkdownReportWriter(blocker).write_repo_report(data.full_report())


def test_table_cells_stay_on_one_line_and_escape_pipes() -> None:
    assert table_cell("Noa |\n Testman") == "Noa \\| Testman"


def test_code_fence_is_longer_than_any_backticks_inside() -> None:
    assert code_fence("plain") == "```"
    assert code_fence("a ```` b") == "`````"
