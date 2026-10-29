"""Fakes for every port, wired into the round graph, for graph and CLI tests."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    ExecutionResult,
    NotEvaluatedReason,
    RepoRunResult,
    Requirement,
    RequirementKind,
    RosterRow,
    Run,
)
from hw_checker.domain.errors import (
    HwCheckerError,
    StorageError,
)
from hw_checker.graph import (
    GraphServices,
    RoundGraph,
    RoundInput,
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
