"""The fixer subgraph in the round (ADR 0021), with a fake LLM and a fake runner."""

from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from hw_checker.bootstrap import checkpoint_serializer
from hw_checker.domain import (
    ExecutionResult,
    FailureKind,
    FixPlan,
    ProposalStatus,
    PublishOutcome,
    PullRequestText,
    ReviewPrompt,
    ReviewViolation,
    SelfReview,
    StepStatus,
)
from hw_checker.domain.errors import LLMError, LLMOutputError
from hw_checker.graph import RoundInput, build_round_graph
from tests.fakes.execution import FakeCodeRunner
from tests.fakes.llm import FAKE_USAGE, FakeLLMClient
from tests.unit.domain import builders
from tests.unit.graph.harness import CATALOG, Harness
from tests.unit.services.fixer_data import (
    BREAK_FIX,
    NAMING_NOTE,
    NO_VIOLATIONS,
    OUTSIDE,
    PR_TEXT,
)

AVIGAIL = "avigail__api"
NOTE_PLAN = FixPlan(edits=(NAMING_NOTE,), files_notes=())
PLACEHOLDER_PLAN = FixPlan(
    edits=(
        BREAK_FIX.model_copy(update={"replace": "x", "reason": "x"}),
        NAMING_NOTE.model_copy(update={"replace": "x", "reason": ""}),
    ),
    files_notes=(),
)
"""What the real model returned under Copilot's brevity rule (stage 14c)."""
CONFIG: RunnableConfig = {"configurable": {"thread_id": "run-1"}}
BROKEN = ExecutionResult(
    build_status=StepStatus.FAILED,
    run_status=StepStatus.SKIPPED,
    failure_kind=FailureKind.BUILD_FAILED,
)


def llm(*plans: FixPlan | LLMError) -> FakeLLMClient:
    return FakeLLMClient({"fixer": list(plans), "judge": [NO_VIOLATIONS], "writer": [PR_TEXT]})


def one_repo(tmp_path: Path, fixer: FakeLLMClient, **overrides: Any) -> Harness:
    return Harness(tmp_path, names=("avigail",), fixer=fixer, **overrides)


def roles(fixer: FakeLLMClient) -> list[str]:
    return [call.role for call in fixer.calls]


def test_without_the_fixer_no_repo_is_fixed(tmp_path: Path) -> None:
    harness = Harness(tmp_path, names=("avigail",))

    output = harness.run()

    assert output["summary"].proposals == 0
    assert harness.storage.proposals.list_for_run("run-1") == ()


def test_a_fixed_repo_gets_a_verified_proposal_and_an_updated_report(tmp_path: Path) -> None:
    fixer = llm(NOTE_PLAN)
    harness = one_repo(tmp_path, fixer)

    output = harness.run()

    assert roles(fixer) == ["fixer", "judge", "writer"]
    [proposal] = harness.storage.proposals.list_for_run("run-1")
    assert proposal.status is ProposalStatus.PENDING_REVIEW and proposal.verified
    assert proposal.pr_title == "HW1 review: small fixes and questions"
    assert "camelCase" in proposal.diff
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None and report.fix.proposal_id == proposal.proposal_id
    summary = output["summary"]
    assert (summary.proposals, summary.llm_calls) == (1, 3)
    assert summary.llm_input_tokens == 3 * FAKE_USAGE.input_tokens
    assert summary.llm_output_tokens == 3 * FAKE_USAGE.output_tokens
    assert harness.git.cloned[0][1] != proposal.local_repo_dir


def test_rejected_edits_are_fed_back_and_the_second_plan_is_used(tmp_path: Path) -> None:
    fixer = llm(FixPlan(edits=(OUTSIDE,), files_notes=()), NOTE_PLAN)
    harness = one_repo(tmp_path, fixer)

    harness.run()

    assert roles(fixer) == ["fixer", "fixer", "judge", "writer"]
    second_plan = fixer.calls[1].prompt
    assert "Feedback on your previous plan (attempt 1 of 2)" in second_plan
    assert "users.json is not a .cs file" in second_plan
    [proposal] = harness.storage.proposals.list_for_run("run-1")
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None and report.fix.attempts == 2
    assert proposal.pr_title == "HW1 review: small fixes and questions"


def test_a_placeholder_plan_is_fed_back_and_the_real_second_plan_is_proposed(
    tmp_path: Path,
) -> None:
    fixer = llm(PLACEHOLDER_PLAN, NOTE_PLAN)
    harness = one_repo(tmp_path, fixer)

    harness.run()

    assert roles(fixer) == ["fixer", "fixer", "judge", "writer"]
    assert "the reason is empty or a placeholder" in fixer.calls[1].prompt
    [proposal] = harness.storage.proposals.list_for_run("run-1")
    assert "camelCase" in proposal.diff


def test_placeholder_plans_on_every_attempt_give_no_proposal_and_say_why(
    tmp_path: Path,
) -> None:
    fixer = llm(PLACEHOLDER_PLAN, PLACEHOLDER_PLAN)
    harness = one_repo(tmp_path, fixer)

    output = harness.run()

    assert roles(fixer)[:2] == ["fixer", "fixer"] and "writer" not in roles(fixer)
    assert output["summary"].proposals == 0
    assert harness.storage.proposals.list_for_run("run-1") == ()
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None and report.fix.attempts == 2
    assert report.fix.reason == (
        "No PR: every edit the fixer planned was rejected (the reason is empty or a "
        "placeholder; give one real sentence saying why)."
    )


def test_a_judge_removing_an_allowed_fix_is_ignored_and_the_report_says_so(
    tmp_path: Path,
) -> None:
    flagged = ReviewViolation(
        edit_index=0, rule=3, quote="Never implement the core", explanation="Too direct."
    )
    unclaimed = BREAK_FIX.model_copy(update={"finding_ids": ()})
    fixer = FakeLLMClient(
        {
            "fixer": [FixPlan(edits=(unclaimed,), files_notes=())],
            "judge": [SelfReview(violations=(flagged,))],
            "writer": [PR_TEXT],
        }
    )
    harness = one_repo(tmp_path, fixer)

    harness.run()

    [proposal] = harness.storage.proposals.list_for_run("run-1")
    assert "break;" in proposal.diff
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None
    [ignored] = report.fix.ignored_review
    assert "a missing break is never a violation" in ignored


def test_an_empty_pr_text_on_every_attempt_gives_no_proposal_and_says_why(
    tmp_path: Path,
) -> None:
    empty = PullRequestText(opening="", fixed=(), for_you=(), names=())
    fixer = FakeLLMClient({"fixer": [NOTE_PLAN], "judge": [NO_VIOLATIONS], "writer": [empty]})
    harness = one_repo(tmp_path, fixer, fixer_overrides={"reply_attempts": 2})

    output = harness.run()

    assert roles(fixer).count("writer") == 4
    assert harness.storage.proposals.list_for_run("run-1") == ()
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None
    assert report.fix.reason.startswith(
        "No PR: the fixer failed (fix_repo/write_pr_text: The writer's PR text is unusable: "
    )
    assert 'names: 0 items for the 1 edits under "Names"' in report.fix.reason
    assert output["round_errors"] and "write_pr_text" in output["round_errors"][0]


def test_a_fix_that_never_clears_its_claim_falls_back_to_its_note(tmp_path: Path) -> None:
    claim = BREAK_FIX.model_copy(update={"finding_ids": ("hw01-http",)})
    failing = builders.finding(requirement_id="hw01-http", assignment_id="hw01")
    fixer = llm(FixPlan(edits=(claim,), files_notes=()))
    harness = one_repo(tmp_path, fixer, outcomes={"hw01-http": (failing,)})

    harness.run()

    assert roles(fixer) == ["fixer", "judge", "fixer", "judge", "writer"]
    [proposal] = harness.storage.proposals.list_for_run("run-1")
    assert "//fix:" not in proposal.diff
    assert "Does case 1 need a break?" in proposal.diff
    assert harness.runner is not None
    assert [
        request.repo_id for request in harness.runner.requests if "--fix" in request.repo_id
    ] == [
        f"{AVIGAIL}--fix1",
        f"{AVIGAIL}--fix2",
        f"{AVIGAIL}--fix2",
    ]


def test_no_proposal_when_even_the_fallback_notes_fail(tmp_path: Path) -> None:
    runner = FakeCodeRunner(results={f"{AVIGAIL}--fix1": BROKEN})
    fixer = llm(FixPlan(edits=(BREAK_FIX, NAMING_NOTE), files_notes=()))
    harness = one_repo(tmp_path, fixer, runner=runner, fixer_overrides={"max_attempts": 1})

    output = harness.run()

    assert output["summary"].proposals == 0
    assert harness.storage.proposals.list_for_run("run-1") == ()
    report = harness.report_writer.repo_reports[-1]
    assert report.fix is not None
    assert report.fix.reason == (
        "No PR: the recheck still failed after the fallback notes (the project no longer builds)."
    )
    assert roles(fixer) == ["fixer", "judge"]


def test_a_fixer_failure_is_a_round_error_and_the_round_goes_on(tmp_path: Path) -> None:
    fixer = FakeLLMClient(errors={"fixer": LLMError("the endpoint is down")})
    harness = Harness(tmp_path, names=("avigail", "batya"), fixer=fixer)

    output = harness.run()

    errors = output["round_errors"]
    assert f"{AVIGAIL}: fix_repo/plan_edits: the endpoint is down" in errors
    assert len(errors) == 2
    assert output["summary"].status.value == "completed"
    assert harness.storage.proposals.list_for_run("run-1") == ()
    reports = {r.repo_run.repo.repo_id: r for r in harness.report_writer.repo_reports if r.fix}
    fix = reports[AVIGAIL].fix
    assert fix is not None
    assert fix.reason.startswith("No PR: the fixer failed")


def test_an_unparseable_reply_is_retried_by_the_graph(tmp_path: Path) -> None:
    fixer = llm(LLMOutputError("not a FixPlan"), NOTE_PLAN)
    harness = one_repo(tmp_path, fixer)

    output = harness.run()

    assert roles(fixer) == ["fixer", "fixer", "judge", "writer"]
    assert output["round_errors"] == []
    assert output["summary"].proposals == 1


def test_an_unparseable_reply_on_the_last_attempt_is_recorded_once(tmp_path: Path) -> None:
    fixer = llm(LLMOutputError("not a FixPlan"))
    harness = one_repo(tmp_path, fixer, fixer_overrides={"reply_attempts": 2})

    output = harness.run()

    assert roles(fixer) == ["fixer", "fixer"]
    assert output["round_errors"] == [f"{AVIGAIL}: fix_repo/plan_edits: not a FixPlan"]


def test_the_cache_answers_a_second_round_on_the_same_commit(tmp_path: Path) -> None:
    fixer = llm(NOTE_PLAN)
    harness = one_repo(tmp_path, fixer)
    graph = build_round_graph(
        CATALOG, harness.services(), 2, cache=InMemoryCache(serde=checkpoint_serializer())
    )

    first = harness.run(graph)
    second = harness.run(graph, run_id="run-2")

    assert roles(fixer) == ["fixer", "judge", "writer"]
    assert first["summary"].llm_calls == 3
    assert (second["summary"].llm_calls, second["summary"].proposals) == (0, 1)
    assert len(harness.storage.proposals.list_for_run("run-2")) == 1


def test_each_fixer_step_is_streamed_as_a_custom_event(tmp_path: Path) -> None:
    harness = one_repo(tmp_path, llm(NOTE_PLAN))
    graph = harness.graph()
    round_input = cast(
        RoundInput, {"assignment_id": "hw01", "roster_path": "r.xlsx", "run_id": "run-1"}
    )

    events = [
        payload["fixer"]
        for _, payload in graph.stream(round_input, stream_mode="custom", subgraphs=True)
        if isinstance(payload, dict) and "fixer" in payload
    ]

    steps = [event["step"] for event in events]
    assert steps[:3] == ["prepare_branch", "plan_edits", "apply_edits"]
    assert "propose" in steps
    assert {event["repo_id"] for event in events} == {AVIGAIL}


def test_the_gate_shows_the_fixers_proposal_and_publishes_it(tmp_path: Path) -> None:
    harness = Harness(tmp_path, names=("avigail", "batya"), fixer=llm(NOTE_PLAN))
    graph = build_round_graph(
        CATALOG, harness.services(), 2, InMemorySaver(serde=checkpoint_serializer())
    )
    round_input = cast(
        RoundInput, {"assignment_id": "hw01", "roster_path": "r.xlsx", "run_id": "run-1"}
    )

    paused: dict[str, Any] = graph.invoke(round_input, CONFIG)

    [interrupt] = paused["__interrupt__"]
    prompt = ReviewPrompt.model_validate(interrupt.value)
    assert [review.repo_id for review in prompt.pending] == [AVIGAIL, "batya__api"]
    assert prompt.pending[0].pr_title == "HW1 review: small fixes and questions"
    done: dict[str, Any] = graph.invoke(
        Command(resume={"approve": [AVIGAIL], "decline": ["batya__api"]}), CONFIG
    )
    [record] = done["publish_results"]
    assert (record.repo_id, record.outcome) == (AVIGAIL, PublishOutcome.PUBLISHED)
    [pull] = harness.code_host.pulls
    assert pull.title == "HW1 review: small fixes and questions"
    [proposal] = [
        p for p in harness.storage.proposals.list_for_run("run-1") if p.repo.repo_id == AVIGAIL
    ]
    assert pull.body == proposal.pr_body
    assert pull.body.startswith(PR_TEXT.opening)
    assert "__interrupt__" not in done
