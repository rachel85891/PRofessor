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
from hw_checker.ports import CodeRunner

logger = logging.getLogger(__name__)


class ExecutionService:
    """Runs one repo through the code runner with the probes of the round's assignments.

    Full logs go to `<runs_dir>/<run_id>/<logs_folder>/<repo_id>`.
    """

    def __init__(self, runner: CodeRunner, runs_dir: Path, logs_folder: Path) -> None:
        self._runner = runner
        self._runs_dir = runs_dir
        self._logs_folder = logs_folder

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
        )
        return self._runner.execute(request)


def _http_probes(assignments: tuple[Assignment, ...]) -> tuple[HttpProbe, ...]:
    return tuple(
        probe
        for assignment in assignments
        for requirement in assignment.requirements
        if requirement.kind is RequirementKind.HTTP
        for probe in requirement.http_probes
    )
