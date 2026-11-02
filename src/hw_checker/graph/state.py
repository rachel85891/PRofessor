"""Graph state for the three levels; ids and small models only (design §7).

Each subgraph has an input and an output schema, so a parent passes it only what it
reads and receives back only append-reducer keys, which parallel branches can share.
"""

import operator
from typing import Annotated, NotRequired, TypedDict

from hw_checker.domain import (
    AssignmentResult,
    ExecutionResult,
    FetchedRepo,
    FixOutcome,
    LlmUsage,
    PublishRecord,
    RepoTarget,
    RosterWarning,
    RunSummary,
)


def merge(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    """Reducer for a mapping that parallel branches add keys to."""
    return {**left, **right}


class AssignmentInput(TypedDict):
    """What an assignment subgraph reads from its repo."""

    run_id: str
    repo: RepoTarget
    selected_assignment_ids: list[str]
    fetched: NotRequired[FetchedRepo]
    execution: NotRequired[ExecutionResult]


class AssignmentOutput(TypedDict):
    """What an assignment subgraph adds to its repo."""

    assignment_results: Annotated[list[AssignmentResult], operator.add]
    errors: Annotated[list[str], operator.add]


class AssignmentState(AssignmentInput, AssignmentOutput):
    """State of one assignment's subgraph within one repo."""


class RepoInput(TypedDict):
    """What the round sends to each repo subgraph."""

    run_id: str
    assignment_id: str
    """The round's assignment."""
    repo: RepoTarget
    selected_assignment_ids: list[str]
    partner: NotRequired[str]
    """The partner's name when the repo belongs to a pair."""


class RepoOutput(TypedDict):
    """What a repo subgraph adds to the round."""

    recorded_repos: Annotated[list[str], operator.add]
    """Ids of the repos whose result was saved."""
    round_errors: Annotated[list[str], operator.add]
    """Problems that kept a repo's result or report from being saved."""
    report_paths: Annotated[dict[str, str], merge]
    """The path of each repo's report, by repo id, for the reports that were written."""
    llm_usage: Annotated[list[LlmUsage], operator.add]
    """Every LLM call's tokens, with the run that paid for it."""
    fix_outcomes: Annotated[list[FixOutcome], operator.add]
    """What the fixer did, one per repo it ran on."""


class RepoState(RepoInput, RepoOutput):
    """State of one repo within a round."""

    fetched: NotRequired[FetchedRepo]
    execution: NotRequired[ExecutionResult]
    assignment_results: Annotated[list[AssignmentResult], operator.add]
    errors: Annotated[list[str], operator.add]


class RoundInput(TypedDict):
    """How a round is started, from the CLI or from LangGraph Studio."""

    assignment_id: str
    """The assignment of the round; `_general` and every earlier assignment are checked too."""
    roster_path: str
    """Path of the roster `.xlsx` file."""
    repo_ids: NotRequired[list[str]]
    """Check only these repos (`owner__name`)."""
    limit: NotRequired[int]
    """Check only the first N repos."""
    run_id: NotRequired[str]
    """The run's id; a new one is made when it is missing or empty."""


class RoundOutput(TypedDict):
    """What a finished round returns."""

    run_id: str
    summary: RunSummary
    roster_warnings: list[RosterWarning]
    round_errors: Annotated[list[str], operator.add]
    publish_results: Annotated[list[PublishRecord], operator.add]


class RoundState(RoundInput):
    """State of a whole round."""

    repos: NotRequired[list[RepoTarget]]
    selected_assignment_ids: NotRequired[list[str]]
    roster_warnings: NotRequired[list[RosterWarning]]
    partners: NotRequired[dict[str, str]]
    """The partner's name, by repo id, for repos that belong to a pair."""
    recorded_repos: Annotated[list[str], operator.add]
    round_errors: Annotated[list[str], operator.add]
    report_paths: Annotated[dict[str, str], merge]
    llm_usage: Annotated[list[LlmUsage], operator.add]
    fix_outcomes: Annotated[list[FixOutcome], operator.add]
    summary: NotRequired[RunSummary]
    gate_open: NotRequired[bool]
    """Whether proposals were pending at the approval gate's last visit."""
    gate_message: NotRequired[str]
    """Why the reviewer's last answer was not applied; empty when it was."""
    approved: NotRequired[list[str]]
    """The repo runs approved at the gate's last visit, to publish next."""
    publish_results: Annotated[list[PublishRecord], operator.add]
    """Every publish attempt of the round, in order."""
