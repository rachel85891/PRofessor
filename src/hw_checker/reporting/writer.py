"""MarkdownReportWriter: the reviewer's reports as Markdown files, and the counts file.

Files go to `<runs_dir>/<run_id>/reports/`: `<repo_id>.md` per repo, `round.md` and
`counts.csv`. The Markdown comes from the Jinja2 templates in `templates/`.
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from hw_checker.domain import (
    Finding,
    RepoRunResult,
    RepoSummary,
    RepoTarget,
)
from hw_checker.domain.errors import StorageError
from hw_checker.ports.reporting import RepoReport, RoundReport, RoundReportFiles
from hw_checker.reporting.wording import FAILURE_KINDS, NOT_EVALUATED_REASONS, SEVERITY_ORDER

TEMPLATES_DIR = Path(__file__).parent / "templates"
REPORTS_FOLDER = "reports"
ROUND_REPORT = "round.md"
COUNTS_FILE = "counts.csv"
SHORT_SHA_LENGTH = 7
UNKNOWN_REQUIREMENT = "(requirement not in the round's assignments)"
NOT_SAVED = "not saved"
COUNTS_COLUMNS = (
    "student",
    "partner",
    "repo",
    "status",
    "critical",
    "major",
    "minor",
    "not_evaluated",
    "report",
)
CSV_ENCODING = "utf-8-sig"
"""UTF-8 with a BOM, so Excel shows Hebrew names."""
_BACKTICKS = re.compile(r"`+")


@dataclass(frozen=True)
class _FindingItem:
    finding: Finding
    title: str


@dataclass(frozen=True)
class _SeveritySection:
    label: str
    items: tuple[_FindingItem, ...]


@dataclass(frozen=True)
class _NotEvaluatedItem:
    requirement_id: str
    title: str
    reason: str


@dataclass(frozen=True)
class _RoundRow:
    repo: RepoTarget
    partner: str | None
    line: RepoSummary | None

    @property
    def status(self) -> str:
        return NOT_SAVED if self.line is None else self.line.status.value

    @property
    def report_name(self) -> str:
        if self.line is None or self.line.report_path is None:
            return ""
        return self.line.report_path.name


class MarkdownReportWriter:
    """`ReportWriter` that renders Markdown reports from templates into the run's folder."""

    def __init__(self, runs_dir: Path) -> None:
        self._runs_dir = runs_dir
        self._templates = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,  # Markdown, not HTML
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._templates.filters["cell"] = table_cell
        self._templates.filters["fence"] = code_fence

    def write_repo_report(self, report: RepoReport) -> Path:
        """Write `<repo_id>.md` for one repo and return its path.

        Raises:
            StorageError: the report cannot be written.
        """
        repo_run = report.repo_run
        text = self._templates.get_template("repo_report.md.j2").render(
            report=report,
            repo=repo_run.repo,
            execution=repo_run.execution,
            short_sha=(repo_run.commit_sha or "")[:SHORT_SHA_LENGTH],
            failure=_failure(repo_run),
            sections=_sections(report),
            not_evaluated=_not_evaluated(report),
        )
        path = self._reports_dir(report.run_id) / f"{repo_run.repo.repo_id}.md"
        _write(path, _markdown(text))
        return path

    def write_round_report(self, report: RoundReport) -> RoundReportFiles:
        """Write `round.md` and `counts.csv` for the round and return their paths.

        Raises:
            StorageError: the files cannot be written.
        """
        summary = report.summary
        lines = {line.repo_id: line for line in summary.repos}
        rows = [
            _RoundRow(repo, report.partners.get(repo.repo_id), lines.get(repo.repo_id))
            for repo in report.repos
        ]
        text = self._templates.get_template("round_report.md.j2").render(
            summary=summary,
            started=(
                summary.started_at.strftime("%Y-%m-%d %H:%M %Z")
                if summary.started_at
                else "unknown"
            ),
            rows=rows,
            roster_warnings=report.roster_warnings,
            publications=[row for row in rows if row.line and row.line.publication],
        )
        reports_dir = self._reports_dir(summary.run_id)
        round_report = reports_dir / ROUND_REPORT
        counts_file = reports_dir / COUNTS_FILE
        _write(round_report, _markdown(text))
        _write(counts_file, _counts_csv(rows).encode(CSV_ENCODING))
        return RoundReportFiles(
            reports_dir=reports_dir, round_report=round_report, counts_file=counts_file
        )

    def write_diff(self, run_id: str, repo_id: str, diff: str) -> Path:
        """Write `<repo_id>.diff` (the proposal's changes, as UTF-8) and return its path.

        Raises:
            StorageError: the file cannot be written.
        """
        path = self._reports_dir(run_id) / f"{repo_id}.diff"
        _write(path, diff.encode("utf-8"))
        return path

    def _reports_dir(self, run_id: str) -> Path:
        return self._runs_dir / run_id / REPORTS_FOLDER


def table_cell(value: object) -> str:
    """`value` as one Markdown table cell: on one line, with `|` escaped."""
    return " ".join(str(value).split()).replace("|", "\\|")


def code_fence(text: str) -> str:
    """A backtick fence longer than any run of backticks in `text`, so it cannot close early."""
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def _failure(repo_run: RepoRunResult) -> str | None:
    execution = repo_run.execution
    if execution is None or execution.failure_kind is None:
        return None
    return FAILURE_KINDS[execution.failure_kind]


def _title(report: RepoReport, requirement_id: str) -> str:
    return report.requirement_titles.get(requirement_id, UNKNOWN_REQUIREMENT)


def _sections(report: RepoReport) -> list[_SeveritySection]:
    findings = [
        finding for result in report.repo_run.assignment_results for finding in result.findings
    ]
    sections = []
    for severity in SEVERITY_ORDER:
        items = tuple(
            _FindingItem(finding, _title(report, finding.requirement_id))
            for finding in findings
            if finding.severity is severity
        )
        if items:
            sections.append(_SeveritySection(severity.value.capitalize(), items))
    return sections


def _not_evaluated(report: RepoReport) -> list[_NotEvaluatedItem]:
    return [
        _NotEvaluatedItem(
            entry.requirement_id,
            _title(report, entry.requirement_id),
            NOT_EVALUATED_REASONS[entry.reason],
        )
        for result in report.repo_run.assignment_results
        for entry in result.not_evaluated
    ]


def _counts_csv(rows: list[_RoundRow]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COUNTS_COLUMNS)
    for row in rows:
        line = row.line
        counts = (
            ["", "", "", ""]
            if line is None
            else [line.critical, line.major, line.minor, line.not_evaluated]
        )
        writer.writerow(
            [
                row.repo.student.name,
                row.partner or "",
                row.repo.url,
                row.status,
                *counts,
                row.report_name,
            ]
        )
    return buffer.getvalue()


def _markdown(text: str) -> bytes:
    """The rendered Markdown, ending in exactly one newline, as UTF-8."""
    return (text.rstrip("\n") + "\n").encode("utf-8")


def _write(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as error:
        raise StorageError(f"Cannot write the report {path.name}: {error}") from error
