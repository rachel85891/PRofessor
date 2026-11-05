"""The fixer subgraph (`fix_repo`): one verified review branch per repo (ADR 0021).

`prepare_branch → plan_edits → apply_edits → self_review → remove_violations → verify`, then
back to `plan_edits` (through `next_attempt`, with the attempt's kept edits) while edits were
rejected or the recheck failed and attempts remain. Then the best acceptable attempt is used
(`best_attempt`; `use_best` applies it again when it is not the last one), or, when no
attempt passed the recheck, `fall_back → verify` once more; and `write_pr_text → propose`, or
`give_up`. Each node calls `FixService` and is guarded: a failure is a round error for the
repo, the repo report says why, and the round goes on.

The LLM nodes have a `RetryPolicy` for unparseable replies and a `CachePolicy` keyed on what
decides their prompt; they change no file, so replaying a cached reply is safe.
"""

import hashlib
import json
import operator
from collections.abc import Callable
from typing import Annotated, NotRequired, TypedDict

from langgraph.cache.base import BaseCache
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import CachePolicy, RetryPolicy

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditApplication,
    ExecutionResult,
    FetchedRepo,
    FixAttempt,
    FixOutcome,
    FixPlan,
    FixWorkspace,
    LlmUsage,
    PlannedEdit,
    RepoTarget,
    SelfReview,
    VerifyResult,
)
from hw_checker.domain.errors import FixError, LLMOutputError
from hw_checker.graph.guard import Update, guarded
from hw_checker.services.fixing import FixService, attempt_choice, best_attempt

FixGraph = CompiledStateGraph["FixState", None, "FixInput", "FixOutput"]
DRAWING = "drawing"
"""Stands for the missing inputs in the cache key of a partial state (graph drawing only)."""
PLAN_KEY_INPUTS = ("repo", "workspace", "selected_assignment_ids", "assignment_results", "attempt")
REVIEW_KEY_INPUTS = ("repo", "workspace", "application", "assignment_results")
TEXT_KEY_INPUTS = (
    "repo",
    "workspace",
    "assignment_id",
    "application",
    "plan",
    "assignment_results",
    "verification",
)


class FixInput(TypedDict):
    """What the fixer reads from its repo."""

    run_id: str
    assignment_id: str
    repo: RepoTarget
    selected_assignment_ids: list[str]
    fetched: FetchedRepo
    execution: NotRequired[ExecutionResult]
    assignment_results: list[AssignmentResult]


class FixOutput(TypedDict):
    """What the fixer adds to its repo (and the repo passes on to the round)."""

    fix_outcomes: Annotated[list[FixOutcome], operator.add]
    llm_usage: Annotated[list[LlmUsage], operator.add]
    round_errors: Annotated[list[str], operator.add]


class FixState(FixInput, FixOutput):
    """State of one repo's fixer run."""

    workspace: NotRequired[FixWorkspace]
    attempt: NotRequired[int]
    feedback: NotRequired[list[str]]
    kept: NotRequired[list[PlannedEdit]]
    """The previous attempt's kept edits, shown to the next plan."""
    tried: NotRequired[list[FixAttempt]]
    """Every attempt so far, with its recheck (not the fallback's)."""
    plan: NotRequired[FixPlan]
    application: NotRequired[EditApplication]
    review: NotRequired[SelfReview | None]
    verification: NotRequired[VerifyResult]
    fell_back: NotRequired[bool]
    pr_title: NotRequired[str]
    pr_body: NotRequired[str]
    failed: NotRequired[bool]
    """Set when a step failed; the subgraph ends."""


def build_fix_graph(
    assignments: tuple[Assignment, ...],
    fixing: FixService,
    cache: BaseCache[object] | None = None,
) -> FixGraph:
    """Build the fixer subgraph over the assignment catalog.

    `cache` keeps the LLM nodes' replies. It is compiled into this subgraph itself: in
    LangGraph 1.2.18 a subgraph does not inherit its parent's cache.
    """
    by_id = {assignment.id: assignment for assignment in assignments}
    reply_attempts = fixing.reply_attempts
    cache_version = fixing.cache_version()
    retry = RetryPolicy(
        max_attempts=reply_attempts,
        retry_on=lambda error: isinstance(error, LLMOutputError),
    )

    def selected(state: FixState) -> tuple[Assignment, ...]:
        return tuple(by_id[i] for i in state["selected_assignment_ids"] if i in by_id)

    def step(name: str, state: FixState, work: Callable[[], Update], llm: bool = False) -> Update:
        repo_id = state["repo"].repo_id
        get_stream_writer()(
            {"fixer": {"repo_id": repo_id, "step": name, "attempt": state.get("attempt", 1)}}
        )

        def failed(message: str) -> Update:
            outcome = FixOutcome(
                repo_id=repo_id,
                attempts=state.get("attempt", 0),
                reason=f"No PR: the fixer failed ({message}).",
            )
            return {
                "round_errors": [f"{repo_id}: {message}"],
                "fix_outcomes": [outcome],
                "failed": True,
            }

        return guarded(
            f"fix_repo/{name}",
            state["run_id"],
            repo_id,
            work,
            on_error=failed,
            retry_on=(lambda error: isinstance(error, LLMOutputError)) if llm else None,
            max_attempts=reply_attempts if llm else 1,
        )

    def prepare_branch(state: FixState) -> Update:
        return step(
            "prepare_branch",
            state,
            lambda: {
                "workspace": fixing.prepare(
                    state["run_id"], state["assignment_id"], state["repo"], state["fetched"]
                ),
                "attempt": 1,
                "feedback": [],
                "kept": [],
                "tried": [],
            },
        )

    def plan_edits(state: FixState) -> Update:
        def work() -> Update:
            plan, usage = fixing.plan(
                state["run_id"],
                state["workspace"],
                by_id[state["assignment_id"]],
                selected(state),
                state["assignment_results"],
                state["attempt"],
                state.get("feedback", []),
                state.get("kept", []),
            )
            return {"plan": plan, "llm_usage": [usage]}

        return step("plan_edits", state, work, llm=True)

    def apply_edits(state: FixState) -> Update:
        return step(
            "apply_edits",
            state,
            lambda: {
                "application": fixing.apply(
                    state["workspace"],
                    state["plan"].edits,
                    by_id[state["assignment_id"]],
                    selected(state),
                    state["assignment_results"],
                )
            },
        )

    def self_review(state: FixState) -> Update:
        def work() -> Update:
            reviewed = fixing.self_review(
                state["run_id"],
                state["workspace"],
                by_id[state["assignment_id"]],
                selected(state),
                state["application"],
                state["assignment_results"],
            )
            if reviewed is None:
                return {"review": None}
            return {"review": reviewed[0], "llm_usage": [reviewed[1]]}

        return step("self_review", state, work, llm=True)

    def remove_violations(state: FixState) -> Update:
        review = state.get("review")
        if review is None:
            return {}
        return step(
            "remove_violations",
            state,
            lambda: {
                "application": fixing.remove_violations(
                    state["workspace"],
                    by_id[state["assignment_id"]],
                    selected(state),
                    state["application"],
                    review,
                    state["assignment_results"],
                )
            },
        )

    def verify(state: FixState) -> Update:
        def work() -> Update:
            verification = fixing.verify(
                state["run_id"],
                state["repo"],
                state["workspace"],
                selected(state),
                state.get("execution"),
                state["assignment_results"],
                state["application"],
                state["attempt"],
            )
            if state.get("fell_back"):
                return {"verification": verification}
            attempt = FixAttempt(
                number=state["attempt"],
                plan=state["plan"],
                application=state["application"],
                verification=verification,
            )
            return {"verification": verification, "tried": [*state.get("tried", []), attempt]}

        return step("verify", state, work)

    def next_attempt(state: FixState) -> Update:
        return {
            "attempt": state["attempt"] + 1,
            "feedback": FixService.feedback(state["application"], state["verification"]),
            "kept": list(state["application"].applied),
        }

    def use_best(state: FixState) -> Update:
        def work() -> Update:
            best = best_attempt(state.get("tried", []))
            if best is None:
                raise FixError("No attempt passed the recheck.")
            return {
                "plan": best.plan,
                "application": fixing.restore(
                    state["workspace"],
                    by_id[state["assignment_id"]],
                    selected(state),
                    best,
                    state["assignment_results"],
                ),
                "verification": best.verification,
            }

        return step("use_best", state, work)

    def fall_back(state: FixState) -> Update:
        return step(
            "fall_back",
            state,
            lambda: {
                "application": fixing.fall_back(
                    state["workspace"],
                    by_id[state["assignment_id"]],
                    selected(state),
                    state["application"],
                    state["verification"],
                    state["assignment_results"],
                ),
                "fell_back": True,
            },
        )

    def give_up(state: FixState) -> Update:
        return {
            "fix_outcomes": [
                FixService.give_up(state["repo"], state["attempt"], state["verification"])
            ]
        }

    def write_pr_text(state: FixState) -> Update:
        def work() -> Update:
            title, body, usages = fixing.write_text(
                state["run_id"],
                by_id[state["assignment_id"]],
                selected(state),
                state["assignment_results"],
                state["application"],
                state["plan"].files_notes,
                state["verification"].cleared,
            )
            return {"pr_title": title, "pr_body": body, "llm_usage": list(usages)}

        return step("write_pr_text", state, work, llm=True)

    def propose(state: FixState) -> Update:
        return step(
            "propose",
            state,
            lambda: {
                "fix_outcomes": [
                    fixing.propose(
                        state["run_id"],
                        state["repo"],
                        state["workspace"],
                        by_id[state["assignment_id"]],
                        state["application"],
                        state.get("pr_title", ""),
                        state.get("pr_body", ""),
                        state["attempt"],
                        attempt_choice(state.get("tried", []), state.get("fell_back", False)),
                        state["verification"].blocked if "verification" in state else (),
                    )
                ]
            },
        )

    def after_verify(state: FixState) -> str:
        if state.get("failed"):
            return END
        verification, application = state["verification"], state["application"]
        if state.get("fell_back"):
            return _finish(application) if verification.passed else "give_up"
        revise = bool(application.rejected) or not verification.passed
        if revise and state["attempt"] < fixing.max_attempts:
            return "next_attempt"
        best = best_attempt(state.get("tried", []))
        if best is None:
            return "fall_back"
        if best.number != state["attempt"]:
            return "use_best"
        return _finish(application)

    def after_use_best(state: FixState) -> str:
        return END if state.get("failed") else _finish(state["application"])

    # To draw the graph (Studio), LangGraph runs these on a state that may be partial or
    # empty; then they return a fixed key per node. A full state's key is unchanged.
    def plan_key(state: FixState) -> str:
        if _partial(state, PLAN_KEY_INPUTS):
            return _key(cache_version, "plan_edits", DRAWING)
        results = sorted(state["assignment_results"], key=lambda r: r.assignment_id)
        return _key(
            cache_version,
            "plan_edits",
            state["repo"].repo_id,
            state["workspace"].base_sha,
            [(a.id, a.source_hash) for a in selected(state)],
            [result.model_dump(mode="json") for result in results],
            state["attempt"],
            state.get("feedback", []),
            [edit.model_dump(mode="json") for edit in state.get("kept", [])],
        )

    def review_key(state: FixState) -> str:
        if _partial(state, REVIEW_KEY_INPUTS):
            return _key(cache_version, "self_review", DRAWING)
        # The judge's rules depend on the findings (a rule `when_found`).
        results = sorted(state["assignment_results"], key=lambda r: r.assignment_id)
        return _key(
            cache_version,
            "self_review",
            state["repo"].repo_id,
            state["workspace"].base_sha,
            state["application"].model_dump(mode="json")["applied"],
            [result.model_dump(mode="json") for result in results],
        )

    def text_key(state: FixState) -> str:
        if _partial(state, TEXT_KEY_INPUTS):
            return _key(cache_version, "write_pr_text", DRAWING)
        results = sorted(state["assignment_results"], key=lambda r: r.assignment_id)
        return _key(
            cache_version,
            "write_pr_text",
            state["repo"].repo_id,
            state["workspace"].base_sha,
            state["assignment_id"],
            state["application"].model_dump(mode="json")["applied"],
            list(state["plan"].files_notes),
            [result.model_dump(mode="json") for result in results],
            list(state["verification"].cleared),
        )

    builder = StateGraph(FixState, input_schema=FixInput, output_schema=FixOutput)
    builder.add_node("prepare_branch", prepare_branch)
    builder.add_node(
        "plan_edits", plan_edits, retry_policy=retry, cache_policy=CachePolicy(key_func=plan_key)
    )
    builder.add_node("apply_edits", apply_edits)
    builder.add_node(
        "self_review",
        self_review,
        retry_policy=retry,
        cache_policy=CachePolicy(key_func=review_key),
    )
    builder.add_node("remove_violations", remove_violations)
    builder.add_node("verify", verify)
    builder.add_node("next_attempt", next_attempt)
    builder.add_node("use_best", use_best)
    builder.add_node("fall_back", fall_back)
    builder.add_node("give_up", give_up)
    builder.add_node(
        "write_pr_text",
        write_pr_text,
        retry_policy=retry,
        cache_policy=CachePolicy(key_func=text_key),
    )
    builder.add_node("propose", propose)
    builder.add_edge(START, "prepare_branch")
    for source, target in [
        ("prepare_branch", "plan_edits"),
        ("plan_edits", "apply_edits"),
        ("apply_edits", "self_review"),
        ("self_review", "remove_violations"),
        ("remove_violations", "verify"),
        ("fall_back", "verify"),
        ("write_pr_text", "propose"),
    ]:
        builder.add_conditional_edges(source, _unless_failed(target), [target, END])
    builder.add_conditional_edges(
        "verify",
        after_verify,
        ["next_attempt", "use_best", "fall_back", "write_pr_text", "propose", "give_up", END],
    )
    builder.add_conditional_edges("use_best", after_use_best, ["write_pr_text", "propose", END])
    builder.add_edge("next_attempt", "plan_edits")
    builder.add_edge("give_up", END)
    builder.add_edge("propose", END)
    return builder.compile(cache=cache, name="fix_repo")


def _finish(application: EditApplication) -> str:
    """With edits to propose, write the PR text first; with none, propose nothing."""
    return "write_pr_text" if application.applied else "propose"


def _unless_failed(target: str) -> Callable[[FixState], str]:
    def route(state: FixState) -> str:
        return END if state.get("failed") else target

    return route


def _partial(state: FixState, inputs: tuple[str, ...]) -> bool:
    """Whether `state` lacks one of the keys a cache key is computed from."""
    return any(name not in state for name in inputs)


def _key(*parts: object) -> str:
    """A cache key: the SHA-256 of the parts as canonical JSON."""
    text = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
