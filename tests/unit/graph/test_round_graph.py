"""The round, repo and assignment graphs over fakes for every port."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    ExecutionResult,
    FailureKind,
    NotEvaluatedReason,
    RepoRunResult,
    RepoRunStatus,
    Requirement,
    RequirementKind,
    RosterRow,
    Run,
    RunStatus,
    RunSummary,
    StepStatus,
)
from hw_checker.domain.errors import (
    ExecutionEnvironmentError,
    HwCheckerError,
    InvalidAssignmentError,
    RepoUnavailableError,
    RosterError,
    StorageError,
)
from hw_checker.graph import (
    GraphServices,
    RoundGraph,
    RoundInput,
    build_repo_graph,
    build_round_graph,
)
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.repo_fetch import RepoFetchService
from hw_checker.services.roster import RosterService
from hw_checker.services.run_record import RunRecordService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.checks import FakeCheckCatalog, FakeRequirementChecker
from tests.fakes.clock import FakeClock
from tests.fakes.execution import FakeCodeRunner
from tests.fakes.git import FakeGitClient
from tests.fakes.roster import FakeRosterReader
from tests.fakes.storage import InMemoryStorage
from tests.unit.domain import builders

ROSTER = Path("roster.xlsx")
NAMES = ("avigail", "batya", "chana")
REPO_IDS = tuple(f"{name}__api" for name in NAMES)


def url(name: str) -> str:
    return f"https://github.com/{name}/api"


def http_requirement(assignment_id: str, path: str) -> Requirement:
    requirement_id = f"{assignment_id}-http"
    return builders.requirement(
        id=requirement_id,
        kind=RequirementKind.HTTP,
        http_probes=(
            builders.probe(requirement_id=requirement_id, assignment_id=assignment_id, path=path),
        ),
    )


def assignment(assignment_id: str, order: int, version: str = "a") -> Assignment:
    requirements: tuple[Requirement, ...] = (
        builders.requirement(id=f"{assignment_id}-build", kind=RequirementKind.BUILD),
    )
    if order:
        requirements += (http_requirement(assignment_id, f"/api/{assignment_id}"),)
    return Assignment(
        id=assignment_id,
        order=order,
        title=f"Assignment {assignment_id}",
        requirements=requirements,
        source_hash=version * 64,
    )


CATALOG = (assignment("_general", 0), assignment("hw01", 1), assignment("hw02", 2))


class ExplodingChecks(DeterministicCheckService):
    """Fails in an unexpected way for one assignment."""

    def __init__(self, assignment_id: str) -> None:
        super().__init__(FakeRequirementChecker())
        self._assignment_id = assignment_id

    def evaluate(
        self, assignment: Assignment, repo_dir: Path, execution: ExecutionResult | None
    ) -> AssignmentResult:
        if assignment.id == self._assignment_id:
            raise RuntimeError("boom")
        return super().evaluate(assignment, repo_dir, execution)


class FailingRecords(RunRecordService):
    """Cannot save one repo's result."""

    def __init__(self, storage: InMemoryStorage, repo_id: str) -> None:
        super().__init__(storage.runs, FakeClock())
        self._repo_id = repo_id

    def record_repo(self, run_id: str, result: RepoRunResult) -> None:
        if result.repo.repo_id == self._repo_id:
            raise StorageError("disk full")
        super().record_repo(run_id, result)


@dataclass
class Harness:
    """Fakes for every port, and the services and graph built over them."""

    tmp_path: Path
    catalog: tuple[Assignment, ...] = CATALOG
    git_errors: Mapping[str, HwCheckerError] = field(default_factory=dict)
    results: Mapping[str, ExecutionResult] = field(default_factory=dict)
    runner: FakeCodeRunner | None = None
    names: tuple[str, ...] = NAMES
    storage: InMemoryStorage = field(default_factory=InMemoryStorage)
    source: FakeAssignmentSource | None = None

    def __post_init__(self) -> None:
        self.runner = self.runner or FakeCodeRunner(results=self.results)
        self.source = self.source or FakeAssignmentSource(self.catalog)
        self.git = FakeGitClient(errors=self.git_errors)
        self.checker = FakeRequirementChecker({"hw01-build": NotEvaluatedReason.BUILD_FAILED})

    def services(self, **overrides: Any) -> GraphServices:
        assert self.runner is not None and self.source is not None
        rows = [
            RosterRow(row_number=index + 2, name=f"{name.title()} Test", github_url=url(name))
            for index, name in enumerate(self.names)
        ]
        values: dict[str, Any] = {
            "roster": RosterService(FakeRosterReader(rows)),
            "assignments": AssignmentCatalogService(self.source, FakeCheckCatalog()),
            "fetch": RepoFetchService(self.git, self.tmp_path / "clones"),
            "execution": ExecutionService(self.runner, self.tmp_path / "runs", Path("logs")),
            "checks": DeterministicCheckService(self.checker),
            "run_records": RunRecordService(self.storage.runs, FakeClock()),
        }
        return GraphServices(**(values | overrides))

    def graph(self, **overrides: Any) -> RoundGraph:
        return build_round_graph(self.catalog, self.services(**overrides), max_parallel_repos=2)

    def run(self, graph: RoundGraph | None = None, **round_input: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "assignment_id": "hw01",
            "roster_path": str(ROSTER),
            "run_id": "run-1",
        }
        output: dict[str, Any] = (graph or self.graph()).invoke(
            cast(RoundInput, values | round_input)
        )
        return output

    def stored(self) -> Run:
        return self.storage.runs.get("run-1")

    def repo_run(self, repo_id: str) -> RepoRunResult:
        return next(r for r in self.stored().repo_runs if r.repo.repo_id == repo_id)


def test_repo_graph_has_one_node_per_assignment(tmp_path: Path) -> None:
    graph = build_repo_graph(CATALOG, Harness(tmp_path).services())

    nodes = set(graph.get_graph().nodes)

    assert {node for node in nodes if node.startswith("assignment_")} == {
        "assignment__general",
        "assignment_hw01",
        "assignment_hw02",
    }
    assert {"fetch_repo", "build_and_run", "record_repo"} <= nodes


def test_round_graph_nodes_and_parallelism(tmp_path: Path) -> None:
    graph = Harness(tmp_path).graph()

    assert {"start_round", "check_repo", "finish_round"} <= set(graph.get_graph().nodes)
    assert graph.config is not None and graph.config.get("max_concurrency") == 2


def test_round_for_hw01_checks_general_and_hw01_only(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    harness.run()

    for repo_id in REPO_IDS:
        repo_run = harness.repo_run(repo_id)
        assert [r.assignment_id for r in repo_run.assignment_results] == ["_general", "hw01"]
        assert repo_run.status is RepoRunStatus.DONE
    assert harness.runner is not None
    for request in harness.runner.requests:
        assert [probe.path for probe in request.probes] == ["/api/hw01"]


def test_every_repo_is_saved_and_the_run_is_finished(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    output = harness.run()

    stored = harness.stored()
    assert stored.status is RunStatus.COMPLETED
    assert sorted(r.repo.repo_id for r in stored.repo_runs) == sorted(REPO_IDS)
    assert {r.repo_run_id for r in stored.repo_runs} == {f"run-1/{i}" for i in REPO_IDS}
    assert [v.assignment_id for v in stored.assignment_versions] == ["_general", "hw01"]
    assert output["run_id"] == "run-1"
    assert output["summary"] == RunSummary.from_run(stored)
    assert output["round_errors"] == []
    hw01 = harness.repo_run("avigail__api").assignment_results[1]
    assert [n.requirement_id for n in hw01.not_evaluated] == ["hw01-build"]


def test_a_failed_fetch_fails_only_that_repo(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path, git_errors={url("batya"): RepoUnavailableError("repository not found")}
    )

    harness.run()

    failed = harness.repo_run("batya__api")
    assert failed.status is RepoRunStatus.FAILED
    assert failed.errors == ("fetch_repo: repository not found",)
    assert (failed.execution, failed.assignment_results, failed.commit_sha) == (None, (), None)
    assert harness.repo_run("avigail__api").status is RepoRunStatus.DONE
    assert harness.repo_run("chana__api").status is RepoRunStatus.DONE
    assert harness.runner is not None
    assert sorted(r.repo_id for r in harness.runner.requests) == ["avigail__api", "chana__api"]
    assert harness.stored().status is RunStatus.COMPLETED


def test_an_unexpected_exception_in_an_assignment_node_is_recorded(tmp_path: Path) -> None:
    harness = Harness(tmp_path, names=("avigail",))

    harness.run(harness.graph(checks=ExplodingChecks("hw01")))

    repo_run = harness.repo_run("avigail__api")
    assert repo_run.status is RepoRunStatus.FAILED
    assert repo_run.errors == ("assignment_hw01: unexpected RuntimeError: boom",)
    assert [r.assignment_id for r in repo_run.assignment_results] == ["_general"]
    assert harness.stored().status is RunStatus.FAILED


def test_a_failed_build_still_runs_the_assignment_nodes(tmp_path: Path) -> None:
    failed = ExecutionResult(
        build_status=StepStatus.FAILED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
    )
    harness = Harness(tmp_path, names=("avigail",), results={"avigail__api": failed})

    harness.run()

    repo_run = harness.repo_run("avigail__api")
    assert repo_run.execution == failed
    assert repo_run.status is RepoRunStatus.DONE
    assert [r.assignment_id for r in repo_run.assignment_results] == ["_general", "hw01"]


def test_an_engine_failure_during_one_repo_is_recorded(tmp_path: Path) -> None:
    runner = FakeCodeRunner(errors={"chana__api": ExecutionEnvironmentError("engine stopped")})
    harness = Harness(tmp_path, runner=runner)

    harness.run()

    repo_run = harness.repo_run("chana__api")
    assert repo_run.errors == ("build_and_run: engine stopped",)
    assert repo_run.status is RepoRunStatus.FAILED
    assert [r.assignment_id for r in repo_run.assignment_results] == ["_general", "hw01"]


@pytest.mark.parametrize(
    ("round_input", "expected"),
    [
        ({"repo_ids": ["chana__api", "avigail__api"]}, ["avigail__api", "chana__api"]),
        ({"limit": 2}, ["avigail__api", "batya__api"]),
        ({"repo_ids": ["batya__api", "chana__api"], "limit": 1}, ["batya__api"]),
    ],
    ids=["repo-ids", "limit", "both"],
)
def test_repo_ids_and_limit_filter_the_repos(
    tmp_path: Path, round_input: dict[str, Any], expected: list[str]
) -> None:
    harness = Harness(tmp_path)

    output = harness.run(**round_input)

    # Repos are saved as they finish, which depends on the parallel scheduling.
    assert sorted(r.repo.repo_id for r in harness.stored().repo_runs) == expected
    assert sorted(line.repo_id for line in output["summary"].repos) == expected


def test_an_unknown_repo_id_stops_the_round_before_it_starts(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    with pytest.raises(RosterError, match="nobody__api"):
        harness.run(repo_ids=["nobody__api"])

    assert harness.storage.runs.list_runs() == ()


def test_an_unready_environment_stops_the_round_before_it_starts(tmp_path: Path) -> None:
    runner = FakeCodeRunner(environment_error=ExecutionEnvironmentError("image missing"))
    harness = Harness(tmp_path, runner=runner)

    with pytest.raises(ExecutionEnvironmentError):
        harness.run()

    assert harness.storage.runs.list_runs() == ()


def test_an_assignment_changed_since_the_build_stops_the_round(tmp_path: Path) -> None:
    edited = (CATALOG[0], assignment("hw01", 1, version="b"), CATALOG[2])
    harness = Harness(tmp_path, source=FakeAssignmentSource(edited))

    with pytest.raises(InvalidAssignmentError, match="changed since the graph was built: hw01"):
        harness.run()


def test_a_result_that_cannot_be_saved_is_a_round_error(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    records = FailingRecords(harness.storage, "batya__api")

    output = harness.run(harness.graph(run_records=records))

    assert output["round_errors"] == ["batya__api: record_repo: disk full"]
    assert sorted(r.repo.repo_id for r in harness.stored().repo_runs) == [
        "avigail__api",
        "chana__api",
    ]


def test_an_empty_roster_finishes_the_round_with_no_repos(tmp_path: Path) -> None:
    harness = Harness(tmp_path, names=())

    output = harness.run()

    assert output["summary"].repos == ()
    assert harness.stored().status is RunStatus.COMPLETED


def test_a_run_id_is_made_when_none_is_given(tmp_path: Path) -> None:
    harness = Harness(tmp_path, names=("avigail",))

    output = harness.run(run_id="")

    assert output["run_id"].startswith("20261029-090000-")
    assert harness.storage.runs.get(output["run_id"]).status is RunStatus.COMPLETED
