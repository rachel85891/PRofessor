import csv
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hw_checker.domain import (
    AssignmentResult,
    CheckedCommit,
    CommitChoice,
    FailureKind,
    FixOutcome,
    HttpMethod,
    NotEvaluated,
    NotEvaluatedReason,
    PublishOutcome,
    PublishRecord,
    ResponseEvidence,
    UnknownCondition,
)
from hw_checker.domain.errors import StorageError
from hw_checker.reporting.wording import (
    FAILURE_KINDS,
    NOT_EVALUATED_REASONS,
    TEMPLATE_WORDED_REASONS,
    describe_checked_commit,
)
from hw_checker.reporting.writer import (
    COUNTS_COLUMNS,
    TEMPLATES_DIR,
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


def publish_record(repo_id: str, outcome: PublishOutcome) -> PublishRecord:
    published = outcome is PublishOutcome.PUBLISHED
    return PublishRecord(
        repo_run_id=f"{data.RUN_ID}/{repo_id}",
        repo_id=repo_id,
        branch_name="hw-review/hw01-2026-11-01",
        fork="review-bot/check_noa" if published else None,
        pr_url="https://github.com/noa/webapi/pull/5" if published else None,
        pr_number=5 if published else None,
        published_at=datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        outcome=outcome,
        message="" if published else "tamar/webapi moved | since the check",
    )


def test_round_report_lists_the_pull_requests_after_publishing(tmp_path: Path) -> None:
    report = data.round_report(reports_dir(tmp_path))
    summary = report.summary.with_publish_records(
        [
            publish_record("noa__webapi", PublishOutcome.PUBLISHED),
            publish_record("tamar__webapi", PublishOutcome.STALE),
        ]
    )

    files = MarkdownReportWriter(tmp_path).write_round_report(
        report.model_copy(update={"summary": summary})
    )

    text = files.round_report.read_text(encoding="utf-8")
    assert "## Pull requests\n" in text
    assert (
        "| Tamar Testson | tamar/webapi | stale | tamar/webapi moved \\| since the check |" in text
    )
    assert (
        "| Noa Testman | noa/webapi | published | [#5](https://github.com/noa/webapi/pull/5) |"
        in text
    )


def test_round_report_has_no_pull_requests_before_publishing(tmp_path: Path) -> None:
    files = MarkdownReportWriter(tmp_path).write_round_report(
        data.round_report(reports_dir(tmp_path))
    )

    assert "Pull requests" not in files.round_report.read_text(encoding="utf-8")


def test_a_diff_is_written_next_to_the_reports(tmp_path: Path) -> None:
    diff = "--- a/Program.cs\n+++ b/Program.cs\n+// fix: a missing break\n"

    path = MarkdownReportWriter(tmp_path).write_diff(data.RUN_ID, "noa__webapi", diff)

    assert path == reports_dir(tmp_path) / "noa__webapi.diff"
    assert path.read_bytes() == diff.encode("utf-8")


def test_the_repo_report_links_the_fixers_proposal(tmp_path: Path) -> None:
    fix = FixOutcome(
        repo_id="noa__webapi",
        proposal_id="run-1/noa__webapi/fix",
        branch_name="hw-review/hw01-2026-11-02",
        pr_title="HW1 review: fixes and questions",
        diff_path=reports_dir(tmp_path) / "noa__webapi.diff",
        attempts=2,
        edits=3,
    )

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        data.full_report().model_copy(update={"fix": fix})
    )

    text = path.read_text(encoding="utf-8")
    assert text.endswith(
        "## Review branch\n\n"
        "- Branch: `hw-review/hw01-2026-11-02` (3 edits, 2 attempts), waiting at the round's"
        " approval gate\n"
        "- Pull request: HW1 review: fixes and questions\n"
        "- Diff: [noa__webapi.diff](noa__webapi.diff)\n"
    )


def test_the_repo_report_says_why_there_is_no_proposal(tmp_path: Path) -> None:
    fix = FixOutcome(repo_id="noa__webapi", attempts=2, reason="No PR: the recheck failed.")

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        data.clean_report().model_copy(update={"fix": fix})
    )

    assert path.read_text(encoding="utf-8").endswith(
        "## Review branch\n\nNo PR: the recheck failed.\n"
    )


def test_the_repo_report_says_which_attempt_was_used(tmp_path: Path) -> None:
    fix = FixOutcome(
        repo_id="noa__webapi",
        attempts=2,
        reason="No PR: the recheck failed.",
        attempt_choice="Attempt 1 of 2 is used: its recheck passed with 4 applied edits.",
    )

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        data.clean_report().model_copy(update={"fix": fix})
    )

    assert path.read_text(encoding="utf-8").endswith(
        "## Review branch\n\nNo PR: the recheck failed.\n\n"
        "Attempt 1 of 2 is used: its recheck passed with 4 applied edits.\n"
    )


def test_the_repo_report_lists_the_claims_waiting_on_a_dependency(tmp_path: Path) -> None:
    fix = FixOutcome(
        repo_id="noa__webapi",
        attempts=1,
        reason="No PR: the recheck failed.",
        blocked=("`hw01-b` can't pass until `hw01-a` passes (claimed by the fix in A.cs)",),
    )

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        data.clean_report().model_copy(update={"fix": fix})
    )

    assert path.read_text(encoding="utf-8").endswith(
        "## Review branch\n\nNo PR: the recheck failed.\n\n"
        "Claims not held against their fix:\n"
        "- `hw01-b` can't pass until `hw01-a` passes (claimed by the fix in A.cs)\n"
    )


def test_the_repo_report_lists_the_self_review_findings_not_acted_on(tmp_path: Path) -> None:
    fix = FixOutcome(
        repo_id="noa__webapi",
        attempts=1,
        reason="No PR: the recheck failed.",
        ignored_review=("Self-review of edit 0 ignored, a missing break is never a violation",),
    )

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        data.clean_report().model_copy(update={"fix": fix})
    )

    assert path.read_text(encoding="utf-8").endswith(
        "## Review branch\n\nNo PR: the recheck failed.\n\n"
        "Self-review findings not acted on:\n"
        "- Self-review of edit 0 ignored, a missing break is never a violation\n"
    )


def test_the_round_report_shows_the_proposals_and_llm_totals(tmp_path: Path) -> None:
    report = data.round_report(reports_dir(tmp_path))
    summary = report.summary.model_copy(
        update={"proposals": 2, "llm_calls": 6, "llm_input_tokens": 900, "llm_output_tokens": 120}
    )

    files = MarkdownReportWriter(tmp_path).write_round_report(
        report.model_copy(update={"summary": summary})
    )

    text = files.round_report.read_text(encoding="utf-8")
    assert "- Proposals waiting for review: 2\n" in text
    assert "- LLM: 6 calls, 900 input and 120 output tokens\n" in text


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
    if reason in TEMPLATE_WORDED_REASONS:
        assert reason not in NOT_EVALUATED_REASONS
        assert f'item.reason_id == "{reason.value}"' in (
            Path(TEMPLATES_DIR, "repo_report.md.j2").read_text(encoding="utf-8")
        )
        return
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


def test_the_repo_report_says_which_commit_was_checked_and_why(tmp_path: Path) -> None:
    checked = CheckedCommit(
        sha="a" * 40,
        authored_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
        choice=CommitChoice.BEFORE_NEXT_TASK,
        next_task_sha="b" * 40,
        later_commits=4,
        latest_sha="c" * 40,
    )
    report = data.full_report()
    repo_run = report.repo_run.model_copy(update={"checked_commit": checked})

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        report.model_copy(update={"repo_run": repo_run})
    )

    assert (
        "- Checked commit: `aaaaaaa` (2026-11-01), the last before the next task started at "
        "`bbbbbbb`. The repo has 4 later commits; the pull request is based on this older "
        "commit.\n"
    ) in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("choice", "expected"),
    [
        (CommitChoice.NEXT_TASK_NOT_STARTED, "the latest; the next task has not started."),
        (CommitChoice.NEXT_TASK_AT_FIRST_COMMIT, "already looks like the next task"),
    ],
)
def test_the_checked_commit_line_says_why_the_latest_was_checked(
    choice: CommitChoice, expected: str
) -> None:
    checked = CheckedCommit(
        sha="a" * 40,
        authored_at=datetime(2026, 11, 1, tzinfo=UTC),
        choice=choice,
        next_task_sha=None if choice is CommitChoice.NEXT_TASK_NOT_STARTED else "a" * 40,
        latest_sha="a" * 40,
    )

    assert expected in describe_checked_commit(checked)


def test_the_repo_report_shows_the_apps_answer_to_a_failed_request(tmp_path: Path) -> None:
    report = data.full_report()
    [first, *rest] = report.repo_run.assignment_results
    answer = ResponseEvidence(
        method=HttpMethod.POST,
        path="/api/users",
        status=400,
        body='{"errors":{"Name":["The Name field is required."]}}',
    )
    empty = ResponseEvidence(method=HttpMethod.GET, path="/api/users/1", status=500)
    finding = first.findings[0].model_copy(update={"responses": (answer, empty)})
    result = first.model_copy(update={"findings": (finding, *first.findings[1:])})
    repo_run = report.repo_run.model_copy(update={"assignment_results": (result, *rest)})

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        report.model_copy(update={"repo_run": repo_run})
    )

    text = path.read_text(encoding="utf-8")
    assert (
        "  The app answered POST /api/users with 400 (the start of the response, the app's "
        "output):\n\n  ```\n"
        '  {"errors":{"Name":["The Name field is required."]}}\n  ```\n'
    ) in text
    assert "  The app answered GET /api/users/1 with 500 and an empty body.\n" in text


def test_the_repo_report_words_a_windows_path_with_the_path(tmp_path: Path) -> None:
    report = data.full_report()
    [first, *rest] = report.repo_run.assignment_results
    skipped = NotEvaluated(
        requirement_id="hw01-r9",
        reason=NotEvaluatedReason.WINDOWS_PATH,
        detail="/app/..\\data\\users.txt",
    )
    result = first.model_copy(update={"not_evaluated": (*first.not_evaluated, skipped)})
    repo_run = report.repo_run.model_copy(update={"assignment_results": (result, *rest)})

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        report.model_copy(update={"repo_run": repo_run})
    )

    text = path.read_text(encoding="utf-8")
    assert (
        "A request it needs answered with a server error after the app could not open "
        "`/app/..\\data\\users.txt`: a path written with `\\` as the folder separator"
    ) in text


def _skipped(assignment_id: str, reason: NotEvaluatedReason, detail: str) -> AssignmentResult:
    return AssignmentResult(
        assignment_id=assignment_id,
        not_evaluated=tuple(
            NotEvaluated(requirement_id=f"{assignment_id}-r{n}", reason=reason, detail=detail)
            for n in (1, 2)
        ),
    )


def test_the_repo_report_says_per_assignment_whether_it_was_checked(tmp_path: Path) -> None:
    report = data.clean_report()
    results = (
        *report.repo_run.assignment_results,
        _skipped(
            "hw02",
            NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE,
            "skip_when di_started: DI has started (3 registrations)",
        ),
        _skipped(
            "hw03",
            NotEvaluatedReason.ASSIGNMENT_CONDITION_UNKNOWN,
            "applies_when di_started could not be evaluated (environment_issue)",
        ),
    )
    repo_run = report.repo_run.model_copy(update={"assignment_results": results})

    path = MarkdownReportWriter(tmp_path).write_repo_report(
        report.model_copy(update={"repo_run": repo_run})
    )

    text = path.read_text(encoding="utf-8")
    assert (
        "## Assignments\n\n"
        "- hw01: checked\n"
        "- hw02: not checked: it does not apply to this repo (skip_when di_started: DI has "
        "started (3 registrations)).\n"
        "- hw03: not checked: whether it applies to this repo could not be decided "
        "(applies_when di_started could not be evaluated (environment_issue)). Review this "
        "repo first.\n"
    ) in text
    # The skipped requirements are not listed one by one.
    assert "## Not evaluated" not in text
    assert "Nothing to report: every checked requirement passed." in text


def test_the_round_report_lists_the_repos_to_review_first(tmp_path: Path) -> None:
    report = data.round_report(reports_dir(tmp_path))
    unknown = UnknownCondition(
        repo_id="noa__webapi", assignment_id="hw03", detail="applies_when x could not be evaluated"
    )
    lines = tuple(
        line.model_copy(update={"unknown_conditions": (unknown,)})
        if line.repo_id == "noa__webapi"
        else line
        for line in report.summary.repos
    )
    summary = report.summary.model_copy(update={"repos": lines})

    files = MarkdownReportWriter(tmp_path).write_round_report(
        report.model_copy(update={"summary": summary})
    )

    text = files.round_report.read_text(encoding="utf-8")
    assert (
        "## Review first (1)\n\n"
        "Whether an assignment applies to these repos could not be decided, so none of its "
        "requirements was checked:\n\n"
        "- noa__webapi: hw03 (applies_when x could not be evaluated)\n"
    ) in text
    assert text.index("## Review first") < text.index("| Student |")


def test_a_round_without_unknown_conditions_has_no_review_first_section(tmp_path: Path) -> None:
    files = MarkdownReportWriter(tmp_path).write_round_report(
        data.round_report(reports_dir(tmp_path))
    )

    assert "Review first" not in files.round_report.read_text(encoding="utf-8")
