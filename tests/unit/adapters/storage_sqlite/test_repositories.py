"""SQLite-only storage tests: round-trip fidelity, a full round, concurrency, error mapping."""

import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.domain import ProposalStatus, ReviewOutcome, Run
from hw_checker.domain.errors import StorageError
from tests.unit.storage import builders


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{(tmp_path / 'hw.db').as_posix()}"
    migrate(url)
    return url


@pytest.fixture
def storage(database_url: str) -> Iterator[SqlStorage]:
    sql = SqlStorage(database_url)
    yield sql
    sql.close()


def test_fully_populated_run_round_trips_equal(storage: SqlStorage) -> None:
    run = builders.full_run()

    storage.runs.save(run)

    assert storage.runs.get(run.run_id) == run


def test_round_trip_keeps_details_exact(storage: SqlStorage) -> None:
    run = builders.full_run()
    storage.runs.save(run)

    loaded = storage.runs.get(run.run_id)

    assert loaded.started_at == run.started_at
    assert loaded.started_at.tzinfo is not None
    with_findings = loaded.repo_runs[1]
    assert with_findings.judged_findings[0].adjusted_severity.value == "minor"
    assert with_findings.judged_findings[1].verdict.value == "rejected"
    assert with_findings.assignment_results[0].findings == ()
    assert with_findings.execution is not None
    assert with_findings.execution.logs_path == Path("workspace") / "logs" / "run-1" / "dana__api"
    assert with_findings.execution.probe_results[1].actual_status is None
    assert with_findings.repo.student.name == "תלמיד dana"


def test_a_full_round_is_saved_and_loaded_back_equal(storage: SqlStorage) -> None:
    run = builders.full_run()
    with_proposal = run.repo_runs[2].repo_run_id
    proposal = builders.proposal("p-1", with_proposal, ProposalStatus.PENDING_REVIEW)
    first = builders.decision(with_proposal, ReviewOutcome.EDITED, "שיניתי את ההסבר")
    final = builders.decision(with_proposal, ReviewOutcome.APPROVED)

    storage.runs.save(run)
    storage.proposals.save(proposal)
    storage.proposals.save_review_decision(first)
    storage.proposals.save_review_decision(final)

    reopened = SqlStorage(storage.database.url)
    try:
        assert reopened.runs.get(run.run_id) == run
        assert reopened.runs.list_runs() == (run,)
        not_evaluated = [
            entry
            for repo_run in reopened.runs.get(run.run_id).repo_runs
            for result in repo_run.assignment_results
            for entry in result.not_evaluated
        ]
        assert [entry.reason.value for entry in not_evaluated] == [
            "build_failed",
            "build_failed",
            "route_not_found",
            "environment_issue",
            "run_not_healthy",
        ]
        assert reopened.proposals.get("p-1") == proposal
        assert reopened.proposals.get_review_decision(with_proposal) == final
        assert reopened.findings.list_judged_findings(run.repo_runs[1].repo_run_id) == (
            run.repo_runs[1].judged_findings
        )
    finally:
        reopened.close()


def test_three_threads_save_repo_runs_of_one_run(storage: SqlStorage) -> None:
    run = builders.full_run()
    storage.runs.save(run.model_copy(update={"repo_runs": ()}))
    errors: list[BaseException] = []

    def save(index: int) -> None:
        try:
            for _ in range(5):
                storage.runs.save_repo_run(run.run_id, run.repo_runs[index])
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=save, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    loaded = storage.runs.get(run.run_id)
    assert {repo_run.repo_run_id for repo_run in loaded.repo_runs} == {
        repo_run.repo_run_id for repo_run in run.repo_runs
    }
    assert set(loaded.repo_runs) == set(run.repo_runs)


def test_repo_run_cannot_move_to_another_run(storage: SqlStorage) -> None:
    run = builders.full_run()
    storage.runs.save(run)
    other = builders.empty_run("run-2", builders.STARTED)
    storage.runs.save(other)

    with pytest.raises(StorageError, match="belongs to another run"):
        storage.runs.save_repo_run("run-2", run.repo_runs[0])


def test_read_only_database_maps_to_storage_error(database_url: str, tmp_path: Path) -> None:
    path = tmp_path / "hw.db"
    os.chmod(path, stat.S_IREAD)
    read_only = SqlStorage(database_url)
    try:
        with pytest.raises(StorageError, match="cannot save run"):
            read_only.runs.save(builders.empty_run("run-9", builders.STARTED))
    finally:
        read_only.close()
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_storage_error_does_not_contain_row_contents(storage: SqlStorage) -> None:
    run: Run = builders.full_run()
    storage.runs.save(run)
    other = builders.empty_run("run-2", builders.STARTED)
    storage.runs.save(other)

    with pytest.raises(StorageError) as raised:
        storage.runs.save_repo_run("run-2", run.repo_runs[1])

    assert "תלמיד" not in str(raised.value)
