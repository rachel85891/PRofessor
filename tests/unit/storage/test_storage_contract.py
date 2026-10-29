"""One contract suite for the storage ports, run against the fakes and against SQLite."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Protocol

import pytest

from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.domain import (
    AssignmentResult,
    CaptureFailure,
    ProposalStatus,
    ResponseShape,
    ReviewOutcome,
    Verdict,
)
from hw_checker.domain.errors import NotFoundError
from hw_checker.ports import FindingRepository, ProposalRepository, RunRepository
from tests.fakes.storage import InMemoryStorage
from tests.unit.storage import builders


class Storage(Protocol):
    """The three repositories of one storage implementation (read-only attributes)."""

    @property
    def runs(self) -> RunRepository: ...
    @property
    def findings(self) -> FindingRepository: ...
    @property
    def proposals(self) -> ProposalRepository: ...


@pytest.fixture(params=["fake", "sqlite"])
def storage(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Storage]:
    if request.param == "fake":
        yield InMemoryStorage()
        return
    url = f"sqlite:///{(tmp_path / 'contract.db').as_posix()}"
    migrate(url)
    sql = SqlStorage(url)
    yield sql
    sql.close()


# ---------------------------------------------------------------- runs


def test_saved_run_is_loaded_back_equal(storage: Storage) -> None:
    run = builders.full_run()

    storage.runs.save(run)

    assert storage.runs.get(run.run_id) == run


def test_not_evaluated_entries_are_loaded_back_in_order(storage: Storage) -> None:
    run = builders.full_run()

    storage.runs.save(run)

    failed = storage.runs.get(run.run_id).repo_runs[0]
    (result,) = failed.assignment_results
    assert result.not_evaluated == run.repo_runs[0].assignment_results[0].not_evaluated
    assert [entry.requirement_id for entry in result.not_evaluated] == [
        "hw01-r3",
        "hw01-r5",
        "hw01-r6",
    ]
    assert result.not_evaluated[-1].reason.value == "route_not_found"


def test_route_table_and_probe_resolution_are_loaded_back(storage: Storage) -> None:
    run = builders.full_run()

    storage.runs.save(run)

    execution = storage.runs.get(run.run_id).repo_runs[1].execution
    assert execution is not None and execution.routes is not None
    assert execution.routes == builders.successful_execution().routes
    assert execution.code_facts == builders.successful_execution().code_facts
    assert execution.code_facts is not None and execution.code_facts.truncated == ("locals",)
    assert [r.route_resolution.value for r in execution.probe_results] == [
        "resolved",
        "unavailable",
        "route_not_found",
        "dependency_missing",
        "unavailable",
        "unavailable",
    ]
    assert execution.probe_results[0].sent_path == "/api/product"
    assert execution.probe_results == builders.successful_execution().probe_results
    assert execution.probe_results[0].body_mismatched
    assert [r.controller for r in execution.probe_results] == [
        "ProductController",
        None,
        None,
        None,
        None,
        None,
    ]
    assert [r.response_shape for r in execution.probe_results] == [
        ResponseShape.OBJECT,
        None,
        None,
        None,
        ResponseShape.ARRAY_OF_SCALARS,
        ResponseShape.CUT,
    ]
    assert execution.probe_results[0].missing_values == ("שולחן לבן",)
    identifier = execution.probe_results[-1].capture
    assert identifier is not None and identifier.failure is CaptureFailure.NOT_AN_IDENTIFIER
    ambiguous = execution.probe_results[-2].capture
    assert ambiguous is not None and ambiguous.failure is not None
    assert (ambiguous.failure.value, ambiguous.candidates) == (
        "ambiguous",
        ("productId", "categoryId"),
    )
    assert (execution.target_framework, execution.framework_outdated) == ("net8.0", True)
    assert (execution.primary_controller, execution.serving_controllers) == (
        "ProductController",
        ("ProductController", "ProductsController"),
    )


def test_saving_a_repo_run_replaces_its_not_evaluated_entries(storage: Storage) -> None:
    run = builders.empty_run("run-1", builders.STARTED)
    storage.runs.save(run)
    first = builders.failed_repo_run()
    storage.runs.save_repo_run("run-1", first)

    evaluated = first.model_copy(
        update={"assignment_results": (AssignmentResult(assignment_id="hw01"),)}
    )
    storage.runs.save_repo_run("run-1", evaluated)

    assert storage.runs.get("run-1").repo_runs == (evaluated,)


def test_unknown_run_raises_not_found(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.runs.get("missing")


def test_saving_a_run_again_updates_its_fields(storage: Storage) -> None:
    run = builders.full_run()
    storage.runs.save(run)
    later = run.model_copy(update={"status": run.status.__class__("failed")})

    storage.runs.save(later)

    assert storage.runs.get(run.run_id).status.value == "failed"


def test_saving_a_run_never_deletes_absent_repo_runs(storage: Storage) -> None:
    run = builders.full_run()
    storage.runs.save(run)

    storage.runs.save(run.model_copy(update={"repo_runs": run.repo_runs[:1]}))

    assert storage.runs.get(run.run_id).repo_runs == run.repo_runs


def test_save_repo_run_needs_an_existing_run(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.runs.save_repo_run("missing", builders.failed_repo_run("missing"))


def test_save_repo_run_adds_then_replaces_a_repo_run(storage: Storage) -> None:
    run = builders.empty_run("run-1", builders.STARTED)
    storage.runs.save(run)
    first = builders.failed_repo_run()

    storage.runs.save_repo_run("run-1", first)
    fixed = first.model_copy(update={"score": 55.0, "errors": ()})
    storage.runs.save_repo_run("run-1", fixed)

    assert storage.runs.get("run-1").repo_runs == (fixed,)


def test_repo_runs_keep_the_order_they_were_first_saved(storage: Storage) -> None:
    run = builders.empty_run("run-1", builders.STARTED)
    storage.runs.save(run)
    a, b, c = (
        builders.proposal_repo_run(),
        builders.failed_repo_run(),
        builders.repo_run_with_findings(),
    )

    for repo_run in (a, b, c):
        storage.runs.save_repo_run("run-1", repo_run)
    updated_a = a.model_copy(update={"score": 10.0})
    storage.runs.save_repo_run("run-1", updated_a)

    assert storage.runs.get("run-1").repo_runs == (updated_a, b, c)


def test_list_runs_is_newest_first(storage: Storage) -> None:
    older = builders.empty_run("run-old", builders.STARTED)
    newest = builders.empty_run("run-new", builders.STARTED + timedelta(days=2))
    middle = builders.empty_run("run-mid", builders.STARTED + timedelta(days=1))
    for run in (older, newest, middle):
        storage.runs.save(run)

    assert [run.run_id for run in storage.runs.list_runs()] == ["run-new", "run-mid", "run-old"]


def test_list_runs_is_empty_without_runs(storage: Storage) -> None:
    assert storage.runs.list_runs() == ()


# ---------------------------------------------------------------- judged findings


def test_judged_findings_saved_with_a_run_are_listed(storage: Storage) -> None:
    run = builders.full_run()
    storage.runs.save(run)
    repo_run = run.repo_runs[1]

    listed = storage.findings.list_judged_findings(repo_run.repo_run_id)

    assert listed == repo_run.judged_findings
    assert [item.verdict for item in listed] == [Verdict.ACCEPTED, Verdict.REJECTED]
    assert listed[1].reason == "Allowed alternative"


def test_save_judged_findings_replaces_exactly_that_repo_run(storage: Storage) -> None:
    run = builders.full_run()
    storage.runs.save(run)
    target, other = run.repo_runs[1], run.repo_runs[2]
    replacement = (builders.judged("f-9", Verdict.REJECTED),)

    storage.findings.save_judged_findings(target.repo_run_id, replacement)

    assert storage.findings.list_judged_findings(target.repo_run_id) == replacement
    assert storage.findings.list_judged_findings(other.repo_run_id) == other.judged_findings
    loaded = {item.repo_run_id: item for item in storage.runs.get(run.run_id).repo_runs}
    assert loaded[target.repo_run_id].judged_findings == replacement


def test_saving_a_repo_run_replaces_its_judged_findings(storage: Storage) -> None:
    run = builders.full_run()
    storage.runs.save(run)
    repo_run = run.repo_runs[1]

    storage.runs.save_repo_run(run.run_id, repo_run.model_copy(update={"judged_findings": ()}))

    assert storage.findings.list_judged_findings(repo_run.repo_run_id) == ()


def test_judged_findings_of_an_unknown_repo_run_are_empty(storage: Storage) -> None:
    assert storage.findings.list_judged_findings("missing") == ()


def test_save_judged_findings_needs_an_existing_repo_run(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.findings.save_judged_findings("missing", ())


# ---------------------------------------------------------------- proposals and decisions


@pytest.fixture
def repo_run_id(storage: Storage) -> str:
    run = builders.full_run()
    storage.runs.save(run)
    return run.repo_runs[2].repo_run_id


def test_saved_proposal_is_loaded_back_equal(storage: Storage, repo_run_id: str) -> None:
    proposal = builders.proposal("p-1", repo_run_id, ProposalStatus.PENDING_REVIEW)

    storage.proposals.save(proposal)

    assert storage.proposals.get("p-1") == proposal


def test_unknown_proposal_raises_not_found(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.proposals.get("missing")


def test_proposal_needs_an_existing_repo_run(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.proposals.save(builders.proposal("p-1", "missing", ProposalStatus.PENDING_REVIEW))


def test_list_by_status_filters_and_keeps_creation_order(
    storage: Storage, repo_run_id: str
) -> None:
    for proposal_id in ("p-1", "p-2", "p-3"):
        storage.proposals.save(
            builders.proposal(proposal_id, repo_run_id, ProposalStatus.PENDING_REVIEW)
        )
    approved = builders.proposal("p-2", repo_run_id, ProposalStatus.APPROVED)
    storage.proposals.save(approved)
    storage.proposals.save(builders.proposal("p-1", repo_run_id, ProposalStatus.PENDING_REVIEW))

    pending = storage.proposals.list_by_status(ProposalStatus.PENDING_REVIEW)

    assert [p.proposal_id for p in pending] == ["p-1", "p-3"]
    assert storage.proposals.list_by_status(ProposalStatus.APPROVED) == (approved,)
    assert storage.proposals.list_by_status(ProposalStatus.PUBLISHED) == ()


def test_review_decision_is_none_until_one_is_saved(storage: Storage, repo_run_id: str) -> None:
    assert storage.proposals.get_review_decision(repo_run_id) is None


def test_latest_review_decision_wins(storage: Storage, repo_run_id: str) -> None:
    first = builders.decision(repo_run_id, ReviewOutcome.REJECTED, "צריך לבדוק שוב")
    second = builders.decision(repo_run_id, ReviewOutcome.APPROVED)

    storage.proposals.save_review_decision(first)
    storage.proposals.save_review_decision(second)

    assert storage.proposals.get_review_decision(repo_run_id) == second


def test_review_decisions_are_per_repo_run(storage: Storage, repo_run_id: str) -> None:
    other = builders.full_run().repo_runs[1].repo_run_id
    storage.proposals.save_review_decision(builders.decision(repo_run_id, ReviewOutcome.EDITED))

    assert storage.proposals.get_review_decision(other) is None


def test_review_decision_needs_an_existing_repo_run(storage: Storage) -> None:
    with pytest.raises(NotFoundError):
        storage.proposals.save_review_decision(builders.decision("missing", ReviewOutcome.APPROVED))
