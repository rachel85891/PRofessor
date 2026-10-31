"""Level 1: the round graph, which sends every repo of the roster to the repo subgraph."""

import logging
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer, Send

from hw_checker.config.logging_setup import log_context
from hw_checker.domain import Assignment, RunSummary
from hw_checker.domain.errors import HwCheckerError, InvalidAssignmentError
from hw_checker.graph.guard import Update
from hw_checker.graph.repo import build_repo_graph
from hw_checker.graph.services import GraphServices
from hw_checker.graph.state import RepoInput, RoundInput, RoundOutput, RoundState

logger = logging.getLogger(__name__)

RoundGraph = CompiledStateGraph[RoundState, None, RoundInput, RoundOutput]


def build_round_graph(
    assignments: tuple[Assignment, ...],
    services: GraphServices,
    max_parallel_repos: int,
    checkpointer: Checkpointer = None,
) -> RoundGraph:
    """Build `start_round → check_repo (Send per repo) → finish_round → write_round_report`.

    `assignments` is the catalog when the graph is built; it fixes the repo graph's
    assignment nodes. At most `max_parallel_repos` repos are checked at a time. Without a
    checkpointer the graph uses its caller's (the LangGraph dev server supplies one).
    """
    built_versions = {assignment.id: assignment.source_hash for assignment in assignments}

    def start_round(state: RoundState) -> Update:
        roster = services.roster.load(Path(state["roster_path"]))
        repos = services.roster.select_repos(
            roster.repos, tuple(state.get("repo_ids") or ()), state.get("limit")
        )
        selected = services.assignments.select_for_round(state["assignment_id"])
        _require_built(selected, built_versions)
        services.execution.prepare()
        run = services.run_records.start(state["assignment_id"], selected, state.get("run_id"))
        return {
            "run_id": run.run_id,
            "repos": list(repos),
            "selected_assignment_ids": [assignment.id for assignment in selected],
            "roster_warnings": list(roster.warnings),
            "partners": services.roster.partner_names(roster),
        }

    def send_repos(state: RoundState) -> list[Send] | str:
        repos = state.get("repos", [])
        if not repos:
            return "finish_round"
        partners = state.get("partners", {})
        sends = []
        for repo in repos:
            repo_input = RepoInput(
                run_id=state["run_id"],
                assignment_id=state["assignment_id"],
                repo=repo,
                selected_assignment_ids=state.get("selected_assignment_ids", []),
            )
            if repo.repo_id in partners:
                repo_input["partner"] = partners[repo.repo_id]
            sends.append(Send("check_repo", repo_input))
        return sends

    def finish_round(state: RoundState) -> Update:
        with log_context(run_id=state["run_id"]):
            run = services.run_records.finish(state["run_id"])
        report_paths = {
            repo_id: Path(path) for repo_id, path in state.get("report_paths", {}).items()
        }
        order = [repo.repo_id for repo in state.get("repos", [])]
        return {"summary": RunSummary.from_run(run, order, report_paths)}

    def write_round_report(state: RoundState) -> Update:
        with log_context(run_id=state["run_id"]):
            try:
                summary = services.reports.write_round_report(
                    state["summary"],
                    tuple(state.get("repos", [])),
                    state.get("partners", {}),
                    tuple(state.get("roster_warnings", [])),
                )
            except HwCheckerError as error:
                # The run is already saved; a missing round file must not fail the round.
                logger.error("The round's report could not be written: %s", error)
                return {"round_errors": [f"write_round_report: {error}"]}
        return {"summary": summary}

    builder = StateGraph(RoundState, input_schema=RoundInput, output_schema=RoundOutput)
    builder.add_node("start_round", start_round)
    builder.add_node("check_repo", build_repo_graph(assignments, services))
    builder.add_node("finish_round", finish_round)
    builder.add_node("write_round_report", write_round_report)
    builder.add_edge(START, "start_round")
    builder.add_conditional_edges("start_round", send_repos, ["check_repo", "finish_round"])
    builder.add_edge("check_repo", "finish_round")
    builder.add_edge("finish_round", "write_round_report")
    builder.add_edge("write_round_report", END)
    graph = builder.compile(checkpointer=checkpointer, name="round")
    return graph.with_config(max_concurrency=max_parallel_repos)


def _require_built(selected: tuple[Assignment, ...], built_versions: dict[str, str | None]) -> None:
    changed = [
        assignment.id
        for assignment in selected
        if assignment.id not in built_versions
        or built_versions[assignment.id] != assignment.source_hash
    ]
    if changed:
        raise InvalidAssignmentError(
            f"Assignments added or changed since the graph was built: {', '.join(changed)}. "
            "Restart the program (or the LangGraph dev server) and start the round again."
        )
