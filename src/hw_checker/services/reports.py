"""Report service: writes the reviewer's report for each repo and the round's files."""

import logging
from collections.abc import Mapping
from pathlib import Path

from hw_checker.domain import (
    Assignment,
    RepoRunResult,
    RepoTarget,
    RosterWarning,
    RunSummary,
)
from hw_checker.ports import ReportWriter
from hw_checker.ports.reporting import RepoReport, RoundReport

logger = logging.getLogger(__name__)


class ReportService:
    """Writes the reviewer's reports through the report writer (ADR 0016's pattern)."""

    def __init__(self, writer: ReportWriter) -> None:
        self._writer = writer

    def write_repo_report(
        self,
        run_id: str,
        assignment_id: str,
        repo_run: RepoRunResult,
        assignments: tuple[Assignment, ...],
        partner: str | None = None,
    ) -> Path:
        """Write the reviewer's report for one repo and return its path.

        `assignments` are the round's assignments; they give each requirement its title.

        Raises:
            StorageError: the report cannot be written.
        """
        titles = {
            requirement.id: requirement.title
            for assignment in assignments
            for requirement in assignment.requirements
        }
        path = self._writer.write_repo_report(
            RepoReport(
                run_id=run_id,
                assignment_id=assignment_id,
                repo_run=repo_run,
                partner=partner,
                requirement_titles=titles,
            )
        )
        logger.info("Repo report written")
        return path

    def write_round_report(
        self,
        summary: RunSummary,
        repos: tuple[RepoTarget, ...],
        partners: Mapping[str, str],
        roster_warnings: tuple[RosterWarning, ...] = (),
    ) -> RunSummary:
        """Write the round's index report and counts file; return `summary` pointing to them.

        `repos` are the round's repos in roster order, which the round's files follow.

        Raises:
            StorageError: the files cannot be written.
        """
        files = self._writer.write_round_report(
            RoundReport(
                summary=summary,
                repos=repos,
                partners=dict(partners),
                roster_warnings=roster_warnings,
            )
        )
        logger.info("Round report written: %d repos", len(repos))
        return summary.model_copy(
            update={"reports_dir": files.reports_dir, "round_report": files.round_report}
        )
