"""Run record service: starts a round in storage, saves each repo's result, finishes it."""

import logging

from hw_checker.domain import (
    Assignment,
    AssignmentVersion,
    RepoRunResult,
    RepoRunStatus,
    Run,
    RunStatus,
    new_run_id,
)
from hw_checker.ports import Clock, RunRepository

logger = logging.getLogger(__name__)


class RunRecordService:
    """Keeps the stored record of a round up to date while it runs."""

    def __init__(self, runs: RunRepository, clock: Clock) -> None:
        self._runs = runs
        self._clock = clock

    def start(
        self,
        assignment_id: str,
        assignments: tuple[Assignment, ...],
        run_id: str | None = None,
    ) -> Run:
        """Save a new running round for `assignment_id` and return it.

        The run records the version (`source_hash`) of each assignment that has one.
        A new id is made from the start time when `run_id` is None.

        Raises:
            StorageError: the run cannot be stored.
        """
        started_at = self._clock.now()
        run = Run(
            run_id=run_id or new_run_id(started_at),
            assignment_id=assignment_id,
            started_at=started_at,
            status=RunStatus.RUNNING,
            assignment_versions=tuple(
                AssignmentVersion(assignment_id=assignment.id, source_hash=assignment.source_hash)
                for assignment in assignments
                if assignment.source_hash is not None
            ),
        )
        self._runs.save(run)
        logger.info("Round started for %s", assignment_id)
        return run

    def record_repo(self, run_id: str, result: RepoRunResult) -> None:
        """Save one repo's result within the run `run_id`.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the result cannot be stored.
        """
        self._runs.save_repo_run(run_id, result)

    def finish(self, run_id: str) -> Run:
        """Mark the run finished and return it with every repo result.

        The run is failed only if it has repos and none of them could be checked (none is
        done); otherwise it is completed.

        Raises:
            NotFoundError: no run with `run_id` exists.
            StorageError: the run cannot be loaded or stored.
        """
        run = self._runs.get(run_id)
        checked = any(repo_run.status is RepoRunStatus.DONE for repo_run in run.repo_runs)
        status = RunStatus.FAILED if run.repo_runs and not checked else RunStatus.COMPLETED
        self._runs.save(run.model_copy(update={"status": status, "repo_runs": ()}))
        logger.info("Round finished: %s, %d repos", status.value, len(run.repo_runs))
        return run.model_copy(update={"status": status})
