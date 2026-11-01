"""The round's approval gate (ADR 0018): interrupt, decisions, publishing, and the loop."""

from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import ValidationError

from hw_checker.bootstrap import checkpoint_serializer
from hw_checker.domain import (
    BranchHead,
    ProposalStatus,
    PublishOutcome,
    ReviewPrompt,
)
from hw_checker.domain.errors import PublishError
from hw_checker.graph import RoundGraph, RoundInput, build_round_graph
from tests.fakes.code_host import FakeCodeHost
from tests.unit.graph.harness import CATALOG, REPO_IDS, Harness, student_name

AVIGAIL, BATYA, CHANA = REPO_IDS
CONFIG: RunnableConfig = {"configurable": {"thread_id": "run-1"}}
ROUND = cast(RoundInput, {"assignment_id": "hw01", "roster_path": "r.xlsx", "run_id": "run-1"})


def gate_graph(harness: Harness) -> RoundGraph:
    checkpointer = InMemorySaver(serde=checkpoint_serializer())
    return build_round_graph(CATALOG, harness.services(), 2, checkpointer)


def prompt_of(output: dict[str, Any]) -> ReviewPrompt:
    [interrupt] = output["__interrupt__"]
    return ReviewPrompt.model_validate(interrupt.value)


def answer(graph: RoundGraph, **decision: Any) -> dict[str, Any]:
    output: dict[str, Any] = graph.invoke(Command(resume=decision), CONFIG)
    return output


def status(harness: Harness, repo_id: str) -> ProposalStatus:
    proposal = harness.storage.proposals.get_for_repo_run(f"run-1/{repo_id}")
    assert proposal is not None
    return proposal.status


def test_a_round_without_proposals_ends_without_interrupting(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    output: dict[str, Any] = gate_graph(harness).invoke(ROUND, CONFIG)

    assert "__interrupt__" not in output
    assert output["publish_results"] == []


def test_the_gate_shows_each_pending_repo(tmp_path: Path) -> None:
    harness = Harness(tmp_path, propose=(AVIGAIL, CHANA))

    prompt = prompt_of(gate_graph(harness).invoke(ROUND, CONFIG))

    assert [review.repo_id for review in prompt.pending] == [AVIGAIL, CHANA]
    first = prompt.pending[0]
    assert first.student == student_name("avigail")
    assert first.not_evaluated == 1
    assert (first.branch_name, first.pr_title) == (
        "hw-review/hw01-2026-11-01",
        "HW1 review: small fixes and questions",
    )
    assert first.report_path == Path("reports", "run-1", f"{AVIGAIL}.md")
    assert first.diff_path == Path("reports", "run-1", f"{AVIGAIL}.diff")
    assert harness.report_writer.diffs[("run-1", AVIGAIL)].startswith("--- a/")
    assert prompt.message == "" and '"approve"' in prompt.how_to_answer


def test_approving_a_subset_publishes_only_those_and_asks_again(tmp_path: Path) -> None:
    harness = Harness(tmp_path, propose=REPO_IDS)
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    output = answer(graph, approve=[AVIGAIL], notes={AVIGAIL: "Good to go"})

    assert [pull.repo_id for pull in harness.code_host.pulls] == [AVIGAIL]
    [record] = output["publish_results"]
    assert (record.repo_id, record.outcome) == (AVIGAIL, PublishOutcome.PUBLISHED)
    assert [review.repo_id for review in prompt_of(output).pending] == [BATYA, CHANA]
    assert status(harness, AVIGAIL) is ProposalStatus.PUBLISHED
    decision = harness.storage.proposals.get_review_decision(f"run-1/{AVIGAIL}")
    assert decision is not None and decision.reviewer_notes == "Good to go"
    line = next(line for line in output["summary"].repos if line.repo_id == AVIGAIL)
    assert line.publication == record
    round_summary = harness.report_writer.round_reports[-1].summary
    assert [line.publication for line in round_summary.repos if line.publication] == [record]


def test_a_declined_repo_is_never_published_and_the_round_ends_when_none_are_left(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, propose=(AVIGAIL, BATYA))
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    still_waiting = answer(graph, decline=[BATYA])
    done = answer(graph, approve=[AVIGAIL])

    assert [review.repo_id for review in prompt_of(still_waiting).pending] == [AVIGAIL]
    assert "__interrupt__" not in done
    assert status(harness, BATYA) is ProposalStatus.REJECTED
    assert [pull.repo_id for pull in harness.code_host.pulls] == [AVIGAIL]
    assert [record.repo_id for record in done["publish_results"]] == [AVIGAIL]


def test_an_unknown_repo_id_records_nothing_and_the_gate_asks_again(tmp_path: Path) -> None:
    harness = Harness(tmp_path, propose=(AVIGAIL,))
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    output = answer(graph, approve=[AVIGAIL, "nobody__api"])

    prompt = prompt_of(output)
    assert prompt.message == (
        f"Nothing was recorded; not pending: nobody__api. Pending: {AVIGAIL}."
    )
    assert [review.repo_id for review in prompt.pending] == [AVIGAIL]
    assert status(harness, AVIGAIL) is ProposalStatus.PENDING_REVIEW
    assert harness.code_host.pulls == []
    assert prompt_of(answer(graph, decline=[])).message == ""


def test_an_answer_of_the_wrong_shape_is_refused_and_the_gate_stays_open(tmp_path: Path) -> None:
    harness = Harness(tmp_path, propose=(AVIGAIL,))
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    with pytest.raises(ValidationError):
        answer(graph, aprove=[AVIGAIL])

    assert graph.get_state(CONFIG).interrupts
    assert "__interrupt__" not in answer(graph, approve=[AVIGAIL])


def test_a_stale_repo_is_not_pushed_and_the_round_goes_on(tmp_path: Path) -> None:
    code_host = FakeCodeHost(heads={AVIGAIL: BranchHead(name="main", sha="9" * 40)})
    harness = Harness(tmp_path, propose=(AVIGAIL, BATYA), code_host=code_host)
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    output = answer(graph, approve=[AVIGAIL, BATYA])

    outcomes = {record.repo_id: record.outcome for record in output["publish_results"]}
    assert outcomes == {AVIGAIL: PublishOutcome.STALE, BATYA: PublishOutcome.PUBLISHED}
    assert [pushed[0].name for pushed in harness.git.pushed] == [BATYA]
    assert "__interrupt__" not in output


def test_a_failed_publish_is_recorded_shown_at_the_next_gate_and_can_be_retried(
    tmp_path: Path,
) -> None:
    code_host = FakeCodeHost(errors={AVIGAIL: PublishError("GitHub is down")})
    harness = Harness(tmp_path, propose=(AVIGAIL, BATYA), code_host=code_host)
    graph = gate_graph(harness)
    graph.invoke(ROUND, CONFIG)

    output = answer(graph, approve=[AVIGAIL, BATYA])

    outcomes = [(record.repo_id, record.outcome) for record in output["publish_results"]]
    assert outcomes == [(AVIGAIL, PublishOutcome.FAILED), (BATYA, PublishOutcome.PUBLISHED)]
    [review] = prompt_of(output).pending
    assert review.repo_id == AVIGAIL
    assert review.last_attempt is not None
    assert review.last_attempt.message == "GitHub is down"
    assert status(harness, AVIGAIL) is ProposalStatus.PENDING_REVIEW
