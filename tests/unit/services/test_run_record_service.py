import re

import pytest

from hw_checker.domain import (
    Assignment,
    AssignmentVersion,
    RepoRunResult,
    RepoRunStatus,
    RunStatus,
)
from hw_checker.domain.errors import NotFoundError
from hw_checker.services.run_record import RunRecordService
from tests.fakes.clock import DEFAULT_NOW, FakeClock
from tests.fakes.storage import InMemoryStorage
from tests.unit.domain import builders

HASH = "a" * 64
GENERAL = Assignment(id="_general", order=0, title="General", requirements=(), source_hash=HASH)
HW01 = Assignment(id="hw01", order=1, title="Users", requirements=())


def repo_run(repo_id: str, status: RepoRunStatus) -> RepoRunResult:
    return RepoRunResult(
        repo_run_id=f"run-1/{repo_id}",
        repo=builders.repo_target(repo_id=repo_id),
        status=status,
    )


def service(storage: InMemoryStorage) -> RunRecordService:
    return RunRecordService(storage.runs, FakeClock())


def test_start_saves_a_running_round_with_assignment_versions() -> None:
    storage = InMemoryStorage()

    run = service(storage).start("hw01", (GENERAL, HW01), run_id="run-1")

    assert run.status is RunStatus.RUNNING
    assert run.started_at == DEFAULT_NOW
    assert run.assignment_versions == (
        AssignmentVersion(assignment_id="_general", source_hash=HASH),
    )
    assert storage.runs.get("run-1") == run


def test_start_makes_an_id_from_the_start_time() -> None:
    run = service(InMemoryStorage()).start("hw01", (HW01,))

    assert re.fullmatch(r"20261029-090000-[0-9a-f]{6}", run.run_id)


def test_record_repo_saves_the_result_in_the_run() -> None:
    storage = InMemoryStorage()
    records = service(storage)
    records.start("hw01", (HW01,), run_id="run-1")

    records.record_repo("run-1", repo_run("dana-api", RepoRunStatus.DONE))

    assert [r.repo.repo_id for r in storage.runs.get("run-1").repo_runs] == ["dana-api"]


def test_record_repo_raises_for_an_unknown_run() -> None:
    with pytest.raises(NotFoundError):
        service(InMemoryStorage()).record_repo("missing", repo_run("dana-api", RepoRunStatus.DONE))


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((RepoRunStatus.DONE, RepoRunStatus.FAILED), RunStatus.COMPLETED),
        ((RepoRunStatus.FAILED, RepoRunStatus.FAILED), RunStatus.FAILED),
        ((), RunStatus.COMPLETED),
    ],
    ids=["one-repo-checked", "no-repo-checked", "no-repos"],
)
def test_finish_fails_the_round_only_when_no_repo_could_be_checked(
    statuses: tuple[RepoRunStatus, ...], expected: RunStatus
) -> None:
    storage = InMemoryStorage()
    records = service(storage)
    records.start("hw01", (HW01,), run_id="run-1")
    for index, status in enumerate(statuses):
        records.record_repo("run-1", repo_run(f"repo-{index}", status))

    finished = records.finish("run-1")

    assert finished.status is expected
    assert len(finished.repo_runs) == len(statuses)
    stored = storage.runs.get("run-1")
    assert stored.status is expected
    assert stored.repo_runs == finished.repo_runs


def test_finish_raises_for_an_unknown_run() -> None:
    with pytest.raises(NotFoundError):
        service(InMemoryStorage()).finish("missing")
