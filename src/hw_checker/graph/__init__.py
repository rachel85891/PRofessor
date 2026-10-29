"""Graph layer: LangGraph state, thin nodes and builders.

A node reads state, calls one service and returns a state update.

May depend on: services, domain.
"""

from hw_checker.graph.assignment import build_assignment_graph
from hw_checker.graph.repo import build_repo_graph
from hw_checker.graph.round import RoundGraph, build_round_graph
from hw_checker.graph.services import GraphServices
from hw_checker.graph.state import RoundInput, RoundOutput

__all__ = [
    "GraphServices",
    "RoundGraph",
    "RoundInput",
    "RoundOutput",
    "build_assignment_graph",
    "build_repo_graph",
    "build_round_graph",
]
