"""In-memory fake for the ReportWriter port."""

from collections.abc import Iterable
from pathlib import Path

from hw_checker.domain.errors import StorageError
from hw_checker.ports.reporting import RepoReport, RoundReport, RoundReportFiles

FAKE_REPORTS_DIR = Path("reports")


class FakeReportWriter:
    """Records every report and returns paths under `reports/<run_id>/` without writing.

    `failing_repos` lists repo ids whose report cannot be written; `round_fails` makes the
    round's files fail.
    """

    def __init__(self, failing_repos: Iterable[str] = (), round_fails: bool = False) -> None:
        self._failing_repos = set(failing_repos)
        self._round_fails = round_fails
        self.repo_reports: list[RepoReport] = []
        self.round_reports: list[RoundReport] = []

    def write_repo_report(self, report: RepoReport) -> Path:
        repo_id = report.repo_run.repo.repo_id
        if repo_id in self._failing_repos:
            raise StorageError(f"Cannot write the report {repo_id}.md: disk full")
        self.repo_reports.append(report)
        return FAKE_REPORTS_DIR / report.run_id / f"{repo_id}.md"

    def write_round_report(self, report: RoundReport) -> RoundReportFiles:
        if self._round_fails:
            raise StorageError("Cannot write the report round.md: disk full")
        self.round_reports.append(report)
        reports_dir = FAKE_REPORTS_DIR / report.summary.run_id
        return RoundReportFiles(
            reports_dir=reports_dir,
            round_report=reports_dir / "round.md",
            counts_file=reports_dir / "counts.csv",
        )
