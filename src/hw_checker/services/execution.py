"""Execution service: builds, runs and probes a repo for the assignments of a round."""

import logging
from pathlib import Path

from hw_checker.domain import (
    Assignment,
    ExecutionRequest,
    ExecutionResult,
    HttpProbe,
    RequirementKind,
)
from hw_checker.domain.errors import HwCheckerError
from hw_checker.ports import CodeRunner, GitClient

logger = logging.getLogger(__name__)


class ExecutionService:
    """Runs one repo through the code runner with the probes of the round's assignments.

    Full logs go to `<runs_dir>/<run_id>/<logs_folder>/<repo_id>`. With a `git` client, the
    files the latest `recent_commits` commits changed are passed to the runner, to choose
    between several Web projects; without one, or when they cannot be read, none are.
    """

    def __init__(
        self,
        runner: CodeRunner,
        runs_dir: Path,
        logs_folder: Path,
        git: GitClient | None = None,
        recent_commits: int = 0,
    ) -> None:
        self._runner = runner
        self._runs_dir = runs_dir
        self._logs_folder = logs_folder
        self._git = git
        self._recent_commits = recent_commits

    def prepare(self) -> None:
        """Remove what crashed runs left behind, then check the engine and the images.

        Raises:
            ExecutionEnvironmentError: the engine doesn't answer or an image is missing.
        """
        removed = self._runner.sweep_leftovers()
        if removed:
            logger.info("Removed %d resources left by earlier runs", removed)
        self._runner.check_environment()

    def execute(
        self,
        run_id: str,
        repo_id: str,
        working_copy: Path,
        assignments: tuple[Assignment, ...],
    ) -> ExecutionResult:
        """Build, run and probe the working copy with every http probe of `assignments`.

        Raises:
            ExecutionEnvironmentError: the engine cannot be reached, so no step could start.
        """
        request = ExecutionRequest(
            run_id=run_id,
            repo_id=repo_id,
            working_copy=working_copy,
            probes=_http_probes(assignments),
            logs_dir=self._runs_dir / run_id / self._logs_folder / repo_id,
            recent_changes=self._recent_changes(working_copy),
        )
        return self._runner.execute(request)

    def _recent_changes(self, working_copy: Path) -> tuple[tuple[str, ...], ...]:
        if self._git is None or self._recent_commits < 1:
            return ()
        try:
            return self._git.recent_changes(working_copy, self._recent_commits)
        except HwCheckerError as error:
            logger.warning(
                "Could not read the latest commits (%s); several Web projects are chosen "
                "without them",
                type(error).__name__,
            )
            return ()


def _http_probes(assignments: tuple[Assignment, ...]) -> tuple[HttpProbe, ...]:
    return tuple(
        probe
        for assignment in assignments
        for requirement in assignment.requirements
        if requirement.kind is RequirementKind.HTTP
        for probe in requirement.http_probes
    )
