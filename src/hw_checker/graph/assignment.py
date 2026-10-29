"""Level 3: the subgraph that checks one assignment's requirements in one repo."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from hw_checker.domain import Assignment
from hw_checker.graph.guard import Update, guarded
from hw_checker.graph.services import GraphServices
from hw_checker.graph.state import AssignmentInput, AssignmentOutput, AssignmentState

AssignmentGraph = CompiledStateGraph[AssignmentState, None, AssignmentInput, AssignmentOutput]


def assignment_node_name(assignment_id: str) -> str:
    """The repo graph's node for an assignment: `assignment__general`, `assignment_hw01`."""
    return f"assignment_{assignment_id}"


def build_assignment_graph(assignment: Assignment, services: GraphServices) -> AssignmentGraph:
    """Build the subgraph for `assignment`: `deterministic_checks` for now.

    It returns no result when the assignment is not in the round's selection. Later
    `llm_review` and `judge` follow `deterministic_checks` here.
    """
    node_name = assignment_node_name(assignment.id)

    def deterministic_checks(state: AssignmentState) -> Update:
        if assignment.id not in state["selected_assignment_ids"]:
            return {}
        return guarded(
            node_name,
            state["run_id"],
            state["repo"].repo_id,
            lambda: {
                "assignment_results": [
                    services.checks.evaluate(
                        assignment, state["fetched"].working_copy, state.get("execution")
                    )
                ]
            },
        )

    builder = StateGraph(
        AssignmentState, input_schema=AssignmentInput, output_schema=AssignmentOutput
    )
    builder.add_node("deterministic_checks", deterministic_checks)
    builder.add_edge(START, "deterministic_checks")
    builder.add_edge("deterministic_checks", END)
    return builder.compile(name=node_name)
