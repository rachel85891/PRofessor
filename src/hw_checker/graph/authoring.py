"""The authoring graph (design §7 and §8.3, ADR 0023): adding a new assignment.

It is separate from the round: `load_intake → draft → validate`, back to `draft` with the
validation's reasons while it fails and attempts remain, then `stage_draft → approval` (an
interrupt, like the round's gate) → `write_approved`.
"""

import logging
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, RetryPolicy, interrupt

from hw_checker.config.logging_setup import log_context
from hw_checker.domain.authoring import (
    AssignmentIntake,
    AuthoringAction,
    AuthoringDecision,
    AuthoringOutcome,
    AuthoringPrompt,
    AuthorReply,
    DraftFiles,
    ValidationReport,
)
from hw_checker.domain.errors import AuthoringError, LLMOutputError
from hw_checker.graph.guard import Update
from hw_checker.services.authoring import AuthoringService

logger = logging.getLogger(__name__)

AUTHORING_RUN = "authoring-{id}"
"""How an authoring run is named in the logs (and the command line's checkpoint thread)."""


class AuthoringInput(TypedDict):
    """How an authoring run is started, from the CLI or from LangGraph Studio."""

    assignment_id: str
    """The new assignment: `assignments/<id>/` holds its `intake.yaml` and `task.md`."""
    with_execution: NotRequired[bool]
    """The preview also builds and runs the workspace clones, like a round with the fixer
    off; by default it runs only the checks that read files."""


class AuthoringOutput(TypedDict):
    """What an authoring run returns when it ends."""

    assignment_id: str
    outcome: NotRequired[AuthoringOutcome | None]
    report_path: NotRequired[str]
    written: NotRequired[list[str]]
    """The approved files, once approved."""


class AuthoringState(AuthoringInput):
    """State of one authoring run; the report and the drafted files live on disk."""

    intake: NotRequired[AssignmentIntake]
    reply: NotRequired[AuthorReply | None]
    reasons: NotRequired[list[str]]
    """What the next draft must change: the validation's reasons or the developer's notes."""
    attempt: NotRequired[int]
    """Drafts since the run started or the developer last revised it."""
    report: NotRequired[ValidationReport]
    report_path: NotRequired[str]
    staged: NotRequired[DraftFiles | None]
    """The drafted files written to the assignment's folder for the approval."""
    approval_message: NotRequired[str]
    """Why the developer's last answer was not applied; empty when it was."""
    decision: NotRequired[AuthoringDecision | None]
    outcome: NotRequired[AuthoringOutcome | None]
    written: NotRequired[list[str]]


def _drafted(state: AuthoringState) -> AuthorReply:
    reply = state.get("reply")
    if reply is None:
        raise AuthoringError("there is no draft yet")
    return reply


AuthoringGraph = CompiledStateGraph[AuthoringState, None, AuthoringInput, AuthoringOutput]


def build_authoring_graph(
    authoring: AuthoringService,
    max_attempts: int,
    reply_attempts: int,
    checkpointer: Checkpointer = None,
) -> AuthoringGraph:
    """Build `load_intake → draft → validate → stage_draft → approval → write_approved`.

    A failed validation goes back to `draft` with its reasons up to `max_attempts` drafts,
    then the run ends with the report and nothing written to the assignment's folder.
    `stage_draft` writes the valid draft to the assignment's folder (`status: draft`); at
    `approval` the developer approves, revises with notes (back to `draft`, not counted
    against `max_attempts`) or stops (the drafted files are removed). `draft` is called
    again, up to `reply_attempts` times, when the author's reply cannot be read. The
    approval needs a checkpointer; without one the graph uses its caller's (Studio's).
    """

    def load_intake(state: AuthoringState) -> Update:
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            intake = authoring.load_intake(state["assignment_id"])
        # A thread can be started again after it ended: every field starts afresh.
        return {
            "intake": intake,
            "reply": None,
            "reasons": [],
            "attempt": 0,
            "staged": None,
            "approval_message": "",
            "decision": None,
            "outcome": None,
            "written": [],
        }

    def draft(state: AuthoringState) -> Update:
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            reply = authoring.draft(
                state["intake"], state.get("reply"), tuple(state.get("reasons", []))
            )
        return {"reply": reply, "attempt": state.get("attempt", 0) + 1}

    def validate(state: AuthoringState) -> Update:
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            reply = _drafted(state)
            report = authoring.validate(state["intake"], reply, state.get("with_execution", False))
            last = not report.passed and state["attempt"] >= max_attempts
            path = authoring.write_report(
                state["intake"], reply, report, state["attempt"], max_attempts, last
            )
        update: Update = {"report": report, "report_path": str(path)}
        if not report.passed:
            update["reasons"] = list(report.reasons)
        if last:
            logger.warning("%s still fails after %d drafts", state["assignment_id"], max_attempts)
            update["outcome"] = AuthoringOutcome.FAILED
        return update

    def after_validate(state: AuthoringState) -> str:
        if state["report"].passed:
            return "stage_draft"
        return END if state.get("outcome") is AuthoringOutcome.FAILED else "draft"

    def stage_draft(state: AuthoringState) -> Update:
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            return {"staged": authoring.stage(state["intake"], _drafted(state))}

    def approval(state: AuthoringState) -> Update:
        # The node runs again from the start when it is resumed, so nothing before
        # `interrupt` writes: the drafted files were written by `stage_draft`, and a hand
        # edit made while waiting is never overwritten.
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            prompt = AuthoringPrompt(
                assignment_id=state["assignment_id"],
                report_path=state["report_path"],
                attempt=state["attempt"],
                message=state.get("approval_message", ""),
            )
            decision = interrupt(prompt.model_dump(mode="json"), response_schema=AuthoringDecision)
            update: Update = {"decision": decision, "approval_message": ""}
            if decision.action is AuthoringAction.REVISE:
                return {**update, "reasons": list(decision.notes), "attempt": 0}
            if decision.action is AuthoringAction.STOP:
                removed = authoring.stop(state["assignment_id"], state.get("staged"))
                logger.info(
                    "Stopped %s (drafted files removed: %s)", state["assignment_id"], removed
                )
                return {**update, "outcome": AuthoringOutcome.STOPPED}
        return update

    def after_approval(state: AuthoringState) -> str:
        decision = state.get("decision")
        if decision is None or decision.action is AuthoringAction.STOP:
            return END
        return "draft" if decision.action is AuthoringAction.REVISE else "write_approved"

    def write_approved(state: AuthoringState) -> Update:
        with log_context(run_id=AUTHORING_RUN.format(id=state["assignment_id"])):
            staged = state.get("staged")
            try:
                if staged is None:
                    raise AuthoringError("nothing was drafted to approve")
                written = authoring.approve(state["intake"], _drafted(state), staged)
            except AuthoringError as error:
                logger.warning("The approval was not applied: %s", error)
                return {"approval_message": str(error), "decision": None}
        return {"written": [str(path) for path in written], "outcome": AuthoringOutcome.APPROVED}

    def after_write(state: AuthoringState) -> str:
        return END if state.get("outcome") is AuthoringOutcome.APPROVED else "approval"

    builder = StateGraph(AuthoringState, input_schema=AuthoringInput, output_schema=AuthoringOutput)
    builder.add_node("load_intake", load_intake)
    builder.add_node(
        "draft",
        draft,
        retry_policy=RetryPolicy(
            max_attempts=reply_attempts,
            retry_on=lambda error: isinstance(error, LLMOutputError),
        ),
    )
    builder.add_node("validate", validate)
    builder.add_node("stage_draft", stage_draft)
    builder.add_node("approval", approval)
    builder.add_node("write_approved", write_approved)
    builder.add_edge(START, "load_intake")
    builder.add_edge("load_intake", "draft")
    builder.add_edge("draft", "validate")
    builder.add_conditional_edges("validate", after_validate, ["draft", "stage_draft", END])
    builder.add_edge("stage_draft", "approval")
    builder.add_conditional_edges("approval", after_approval, ["draft", "write_approved", END])
    builder.add_conditional_edges("write_approved", after_write, ["approval", END])
    return builder.compile(checkpointer=checkpointer, name="authoring")
