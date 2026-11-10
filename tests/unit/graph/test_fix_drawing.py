"""The fixer subgraph can be drawn (LangGraph Studio), and real runs keep their cache keys.

To draw a graph, LangGraph 1.2.18 runs each node's `CachePolicy.key_func` on a state that
may be partial or empty.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hw_checker.domain import (
    EditApplication,
    FixPlan,
    FixWorkspace,
    VerifyResult,
)
from hw_checker.graph.fix import FixGraph, build_fix_graph
from tests.fakes.llm import FakeLLMClient
from tests.unit.graph.harness import CATALOG, Harness
from tests.unit.services.fixer_data import BREAK_FIX, NAMING_NOTE, REPO, RESULTS

KEYED_NODES = ("plan_edits", "self_review", "write_pr_text")
APPLICATION = EditApplication(applied=(BREAK_FIX, NAMING_NOTE))
PLAN = FixPlan(edits=(BREAK_FIX, NAMING_NOTE), files_notes=("Class1.cs is a leftover.",))
FULL_STATE: dict[str, Any] = {
    "run_id": "run-1",
    "assignment_id": "hw01",
    "repo": REPO,
    "selected_assignment_ids": ["_general", "hw01"],
    "assignment_results": list(RESULTS),
    "workspace": FixWorkspace(
        working_copy=Path("fixes/dana__api"),
        base_sha="ab" * 20,
        assignment_id="hw01",
        branch_name="hw-review/x",
    ),
    "attempt": 2,
    "feedback": ["Rejected fix in x.cs: too long"],
    "kept": [NAMING_NOTE],
    "application": APPLICATION,
    "plan": PLAN,
    "verification": VerifyResult(passed=True, cleared=("hw01-update",)),
}


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path, fixer=FakeLLMClient())


def fix_graph(harness: Harness) -> FixGraph:
    fixing = harness.services().fixing
    assert fixing is not None
    return build_fix_graph(CATALOG, fixing)


def key_func(graph: FixGraph, node: str) -> Callable[[Any], str]:
    policy = graph.builder.nodes[node].cache_policy
    assert policy is not None
    return policy.key_func  # type: ignore[no-any-return]


def expected_key(version: str, *parts: object) -> str:
    """The cache key as stage 14 computed it, so existing caches stay valid."""
    text = json.dumps([version, *parts], sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("xray", [1, 2, True])
def test_the_round_graph_with_the_fixer_can_be_drawn_at_every_depth(
    harness: Harness, xray: int | bool
) -> None:
    nodes = harness.graph().get_graph(xray=xray).nodes

    if xray != 1:
        assert any(name.endswith("fix_repo:plan_edits") for name in nodes)


def test_the_fix_subgraph_alone_can_be_drawn(harness: Harness) -> None:
    nodes = fix_graph(harness).get_graph(xray=True).nodes

    assert {"prepare_branch", "plan_edits", "verify", "propose"} <= set(nodes)


@pytest.mark.parametrize("node", KEYED_NODES)
@pytest.mark.parametrize("state", [{}, {"repo": REPO}], ids=["empty", "partial"])
def test_a_key_function_on_a_partial_state_gives_a_stable_key(
    harness: Harness, node: str, state: dict[str, Any]
) -> None:
    key = key_func(fix_graph(harness), node)

    assert key(state) == key(dict(state))


def test_full_state_keys_are_unchanged(harness: Harness) -> None:
    graph = fix_graph(harness)
    fixing = harness.services().fixing
    assert fixing is not None
    version = fixing.cache_version()
    results = [result.model_dump(mode="json") for result in RESULTS]
    applied = APPLICATION.model_dump(mode="json")["applied"]
    versions = [(a.id, a.source_hash) for a in CATALOG if a.id in ("_general", "hw01")]

    assert key_func(graph, "plan_edits")(FULL_STATE) == expected_key(
        version,
        "plan_edits",
        REPO.repo_id,
        "ab" * 20,
        versions,
        results,
        2,
        FULL_STATE["feedback"],
        [NAMING_NOTE.model_dump(mode="json")],
    )
    # Stage 18: the judge's rules depend on the findings (`when_found`), so its key has them.
    assert key_func(graph, "self_review")(FULL_STATE) == expected_key(
        version, "self_review", REPO.repo_id, "ab" * 20, applied, results
    )
    assert key_func(graph, "write_pr_text")(FULL_STATE) == expected_key(
        version,
        "write_pr_text",
        REPO.repo_id,
        "ab" * 20,
        "hw01",
        applied,
        ["Class1.cs is a leftover."],
        results,
        ["hw01-update"],
    )
