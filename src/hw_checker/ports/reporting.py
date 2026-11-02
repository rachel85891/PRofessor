"""Port for writing the reviewer's reports: one per repo, and the round's files."""

from pathlib import Path
from typing import Protocol

from hw_checker.domain import (
    FixOutcome,
    RepoRunResult,
    RepoTarget,
    RosterWarning,
    RunSummary,
)
from hw_checker.domain.base import DomainModel, Identifier, NonBlankStr


class RepoReport(DomainModel):
    """Everything one repo's reviewer report shows."""

    run_id: Identifier
    assignment_id: Identifier
    """The round's assignment."""
    repo_run: RepoRunResult
    partner: NonBlankStr | None = None
    """The partner's name when the repo belongs to a pair."""
    requirement_titles: dict[str, str]
    """The title of every requirement of the round, by requirement id."""
    fix: FixOutcome | None = None
    """What the fixer did for the repo; None when it did not run."""


class RoundReport(DomainModel):
    """Everything the round's files show."""

    summary: RunSummary
    repos: tuple[RepoTarget, ...]
    """The round's repos in roster order, including any whose result was not saved."""
    partners: dict[str, str]
    """The partner's name, by repo id, for repos that belong to a pair."""
    roster_warnings: tuple[RosterWarning, ...] = ()


class RoundReportFiles(DomainModel):
    """Where the round's files were written."""

    reports_dir: Path
    round_report: Path
    counts_file: Path


class ReportWriter(Protocol):
    """Renders the reviewer's reports from templates and writes them for one round.

    Reports are in English. There is no grade yet: the round's files hold counts only.
    """

    def write_repo_report(self, report: RepoReport) -> Path:
        """Write the reviewer's report for one repo and return the file's path.

        Raises:
            StorageError: the report cannot be written.
        """
        ...

    def write_round_report(self, report: RoundReport) -> RoundReportFiles:
        """Write the round's index report and its counts file, and return their paths.

        Raises:
            StorageError: the files cannot be written.
        """
        ...

    def write_diff(self, run_id: str, repo_id: str, diff: str) -> Path:
        """Write a repo's proposed changes (`<repo_id>.diff`) next to its report.

        Raises:
            StorageError: the file cannot be written.
        """
        ...
