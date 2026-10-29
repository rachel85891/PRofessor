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
    RepoTarget,
    RosterWarning,
    RunSummary,
)


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
    repo: RepoTarget
    selected_assignment_ids: list[str]


class RepoOutput(TypedDict):
    """What a repo subgraph adds to the round."""

    recorded_repos: Annotated[list[str], operator.add]
    """Ids of the repos whose result was saved."""
    round_errors: Annotated[list[str], operator.add]
    """Problems that kept a repo's result from being saved."""


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


class RoundState(RoundInput):
    """State of a whole round."""

    repos: NotRequired[list[RepoTarget]]
    selected_assignment_ids: NotRequired[list[str]]
    roster_warnings: NotRequired[list[RosterWarning]]
    recorded_repos: Annotated[list[str], operator.add]
    round_errors: Annotated[list[str], operator.add]
    summary: NotRequired[RunSummary]
