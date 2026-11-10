"""Two repos in one round take different paths (`applies_when` / `skip_when`, stage 20)."""

import shutil
from pathlib import Path

from hw_checker.domain import CheckCondition, FixPlan, NotEvaluatedReason
from hw_checker.services.assignments import AssignmentCatalogService
from tests.fakes.git import FakeGitClient
from tests.fakes.llm import FakeLLMClient
from tests.unit.graph.harness import CATALOG, Harness, assignment, url
from tests.unit.services.condition_data import MARKER, REGISTRY, ConditionChecker
from tests.unit.services.fixer_data import NAMING_NOTE, NO_VIOLATIONS, PR_TEXT

DI_REPO = "avigail__api"
LAYERS_REPO = "batya__api"
ON_MARKER = CheckCondition(check="marker_file", params={"name": MARKER})
PATHS = (
    *CATALOG[:2],
    assignment("hw02", 2).model_copy(update={"skip_when": ON_MARKER}),
    assignment("hw03", 3).model_copy(update={"applies_when": ON_MARKER}),
)


def round_on_hw03(tmp_path: Path, fixer: FakeLLMClient | None = None) -> Harness:
    """`avigail`'s repo has the marker (the DI path), `batya`'s has not (the layers path)."""
    harness = Harness(tmp_path, catalog=PATHS, names=("avigail", "batya"), fixer=fixer)
    layers = harness._fixer_repo() if fixer else tmp_path / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    di = tmp_path / "di"
    shutil.copytree(layers, di)
    (di / MARKER).write_text("synthetic\n", encoding="utf-8")
    harness.git = FakeGitClient(sources={url("avigail"): di, url("batya"): layers})
    harness.checker = ConditionChecker({"hw01-build": NotEvaluatedReason.BUILD_FAILED})
    assert harness.source is not None
    harness.run(
        harness.graph(assignments=AssignmentCatalogService(harness.source, REGISTRY)),
        assignment_id="hw03",
    )
    return harness


def not_checked(harness: Harness, repo_id: str) -> dict[str, str | None]:
    return {
        result.assignment_id: None if result.not_checked is None else result.not_checked.reason
        for result in harness.repo_run(repo_id).assignment_results
    }


def test_each_repo_is_checked_on_its_own_path(tmp_path: Path) -> None:
    harness = round_on_hw03(tmp_path)

    assert not_checked(harness, DI_REPO) == {
        "_general": None,
        "hw01": None,
        "hw02": NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE,
        "hw03": None,
    }
    assert not_checked(harness, LAYERS_REPO) == {
        "_general": None,
        "hw01": None,
        "hw02": None,
        "hw03": NotEvaluatedReason.ASSIGNMENT_NOT_APPLICABLE,
    }
    reports = {r.repo_run.repo.repo_id: r for r in harness.report_writer.repo_reports}
    assert reports[DI_REPO].repo_run.assignment_results[2].not_checked is not None
    assert reports[LAYERS_REPO].repo_run.assignment_results[3].not_checked is not None


def test_each_repo_gets_a_review_for_its_own_target(tmp_path: Path) -> None:
    plan = FixPlan(edits=(NAMING_NOTE,), files_notes=())
    fixer = FakeLLMClient({"fixer": [plan], "judge": [NO_VIOLATIONS], "writer": [PR_TEXT]})

    harness = round_on_hw03(tmp_path, fixer)

    proposals = {p.repo.repo_id: p for p in harness.storage.proposals.list_for_run("run-1")}
    assert proposals[DI_REPO].branch_name.startswith("hw-review/hw03-")
    assert proposals[DI_REPO].pr_title.startswith("HW3 review")
    assert proposals[LAYERS_REPO].branch_name.startswith("hw-review/hw02-")
    assert proposals[LAYERS_REPO].pr_title.startswith("HW2 review")
    plans = [call.prompt for call in fixer.calls if call.role == "fixer"]
    [di_plan] = [prompt for prompt in plans if "(HW3: Assignment hw03)" in prompt]
    [layers_plan] = [prompt for prompt in plans if "(HW2: Assignment hw02)" in prompt]
    assert "`hw03-http`" in di_plan and "`hw02-http`" not in di_plan
    assert "`hw02-http`" in layers_plan and "`hw03-http`" not in layers_plan
