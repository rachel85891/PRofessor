"""Port for writing reports and the grades file.

Provisional: the reports stage (stage 11) may revise it.
"""

from pathlib import Path
from typing import Protocol

from hw_checker.domain import RepoRunResult, Run


class ReportWriter(Protocol):
    """Renders and writes the reviewer's reports and the grades file. Provisional."""

    def write_repo_report(self, run_id: str, repo_run: RepoRunResult) -> Path:
        """Write the reviewer's report for one repo run and return the file's path.

        Raises:
            StorageError: the report cannot be written.
        """
        ...

    def write_grades_file(self, run: Run) -> Path:
        """Write the grades file for `run` and return the file's path.

        Raises:
            StorageError: the grades file cannot be written.
        """
        ...
