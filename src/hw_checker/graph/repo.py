"""Level 2: the subgraph that checks one repo against every assignment of the round."""

from langgraph.cache.base import BaseCache
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hw_checker.domain import Assignment, RepoRunResult, RepoRunStatus
from hw_checker.graph.assignment import assignment_node_name, build_assignment_graph
from hw_checker.graph.fix import build_fix_graph
from hw_checker.graph.guard import Update, guarded
from hw_checker.graph.services import GraphServices
from hw_checker.graph.state import RepoInput, RepoOutput, RepoState

RepoGraph = CompiledStateGraph[RepoState, None, RepoInput, RepoOutput]


def build_repo_graph(
    assignments: tuple[Assignment, ...],
    services: GraphServices,
    cache: BaseCache[object] | None = None,
) -> RepoGraph:
    """Build `fetch_repo → build_and_run → assignment_<id> (in parallel) → record_repo →
    write_report`, then `fix_repo → update_report` when the fixer is on.

    `assignments` is the whole catalog, so every assignment gets a node (ADR 0019); the
    ones outside a round's selection return no result. A failed fetch goes straight to
    `record_repo`; a failed build still runs the assignment nodes. The fixer runs only on
    a fetched repo with assignment results (ADR 0021); `cache` keeps its LLM replies.
    """
    by_id = {assignment.id: assignment for assignment in assignments}

    def selected(state: RepoState) -> tuple[Assignment, ...]:
        return tuple(by_id[i] for i in state["selected_assignment_ids"] if i in by_id)

    def fetch_repo(state: RepoState) -> Update:
        return guarded(
            "fetch_repo",
            state["run_id"],
            state["repo"].repo_id,
            lambda: {
                "fetched": services.fetch.fetch(state["repo"], by_id.get(state["assignment_id"]))
            },
        )

    def build_and_run(state: RepoState) -> Update:
        return guarded(
            "build_and_run",
            state["run_id"],
            state["repo"].repo_id,
            lambda: {
                "execution": services.execution.execute(
                    state["run_id"],
                    state["repo"].repo_id,
                    state["fetched"].working_copy,
                    selected(state),
                )
            },
        )

    def record_repo(state: RepoState) -> Update:
        repo_id = state["repo"].repo_id

        def record() -> Update:
            services.run_records.record_repo(state["run_id"], repo_run_result(state))
            return {"recorded_repos": [repo_id]}

        return guarded(
            "record_repo",
            state["run_id"],
            repo_id,
            record,
            on_error=lambda message: {"round_errors": [f"{repo_id}: {message}"]},
        )

    def write_report(state: RepoState) -> Update:
        repo_id = state["repo"].repo_id

        def write() -> Update:
            path = services.reports.write_repo_report(
                state["run_id"],
                state["assignment_id"],
                repo_run_result(state),
                selected(state),
                state.get("partner"),
            )
            return {"report_paths": {repo_id: str(path)}}

        # A report that cannot be written is a round error; the repo keeps its status.
        return guarded(
            "write_report",
            state["run_id"],
            repo_id,
            write,
            on_error=lambda message: {"round_errors": [f"{repo_id}: {message}"]},
        )

    def update_report(state: RepoState) -> Update:
        repo_id = state["repo"].repo_id
        outcomes = state.get("fix_outcomes", [])

        def write() -> Update:
            path = services.reports.write_repo_report(
                state["run_id"],
                state["assignment_id"],
                repo_run_result(state),
                selected(state),
                state.get("partner"),
                outcomes[-1] if outcomes else None,
            )
            return {"report_paths": {repo_id: str(path)}}

        return guarded(
            "update_report",
            state["run_id"],
            repo_id,
            write,
            on_error=lambda message: {"round_errors": [f"{repo_id}: {message}"]},
        )

    def after_report(state: RepoState) -> str:
        fixable = "fetched" in state and bool(state.get("assignment_results"))
        return "fix_repo" if services.fixing is not None and fixable else END

    builder = StateGraph(RepoState, input_schema=RepoInput, output_schema=RepoOutput)
    builder.add_node("fetch_repo", fetch_repo)
    builder.add_node("build_and_run", build_and_run)
    builder.add_node("record_repo", record_repo)
    builder.add_node("write_report", write_report)
    builder.add_edge(START, "fetch_repo")
    builder.add_conditional_edges("fetch_repo", _after_fetch, ["build_and_run", "record_repo"])
    node_names = [assignment_node_name(assignment.id) for assignment in assignments]
    for assignment, node_name in zip(assignments, node_names, strict=True):
        builder.add_node(node_name, build_assignment_graph(assignment, services))
        builder.add_edge("build_and_run", node_name)
    builder.add_edge(node_names or "build_and_run", "record_repo")
    builder.add_edge("record_repo", "write_report")
    if services.fixing is None:
        builder.add_edge("write_report", END)
    else:
        builder.add_node("fix_repo", build_fix_graph(assignments, services.fixing, cache))
        builder.add_node("update_report", update_report)
        builder.add_conditional_edges("write_report", after_report, ["fix_repo", END])
        builder.add_edge("fix_repo", "update_report")
        builder.add_edge("update_report", END)
    return builder.compile(name="check_repo")


def _after_fetch(state: RepoState) -> str:
    return "build_and_run" if "fetched" in state else "record_repo"


def repo_run_result(state: RepoState) -> RepoRunResult:
    """The repo's result from its state; failed when it was not fetched or had an error.

    Assignment results are ordered like the round's selection.
    """
    fetched = state.get("fetched")
    errors = state.get("errors", [])
    order = {assignment_id: i for i, assignment_id in enumerate(state["selected_assignment_ids"])}
    results = sorted(
        state.get("assignment_results", []),
        key=lambda result: order.get(result.assignment_id, len(order)),
    )
    return RepoRunResult(
        repo_run_id=f"{state['run_id']}/{state['repo'].repo_id}",
        repo=state["repo"],
        commit_sha=fetched.commit_sha if fetched else None,
        checked_commit=fetched.checked if fetched else None,
        status=RepoRunStatus.DONE if fetched and not errors else RepoRunStatus.FAILED,
        execution=state.get("execution"),
        assignment_results=tuple(results),
        errors=tuple(errors),
    )
