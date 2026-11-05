"""Fakes for every port, wired into the round graph, for graph and CLI tests."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hw_checker.adapters.jinja_templates import JinjaTextTemplates
from hw_checker.adapters.local_files import LocalSourceFiles
from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    BranchHead,
    ExecutionResult,
    FixProposal,
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
from hw_checker.prompts import TEMPLATES_DIR
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.fixing import FixService
from hw_checker.services.publishing import PublishService
from hw_checker.services.repo_fetch import RepoFetchService
from hw_checker.services.reports import ReportService
from hw_checker.services.roster import RosterService
from hw_checker.services.run_record import RunRecordService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.checks import FakeCheckCatalog, FakeRequirementChecker, Outcome
from tests.fakes.clock import FakeClock
from tests.fakes.code_host import FakeCodeHost
from tests.fakes.execution import FakeCodeRunner
from tests.fakes.git import DEFAULT_SHA, FakeGitClient
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reporting import FakeReportWriter
from tests.fakes.roster import FakeRosterReader
from tests.fakes.storage import InMemoryStorage
from tests.unit.domain import builders

ROSTER = Path("roster.xlsx")
NAMES = ("avigail", "batya", "chana")
REPO_IDS = tuple(f"{name}__api" for name in NAMES)


def url(name: str) -> str:
    return f"https://github.com/{name}/api"


def student_name(name: str) -> str:
    return f"{name.title()} Test"


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


class ProposingRecords(RunRecordService):
    """Saves a pending fix proposal for some repos once their result is recorded, as the
    fixer (stage 14) will."""

    def __init__(self, storage: InMemoryStorage, repo_ids: tuple[str, ...], clones: Path) -> None:
        super().__init__(storage.runs, FakeClock())
        self._proposals = storage.proposals
        self._repo_ids = repo_ids
        self._clones = clones

    def record_repo(self, run_id: str, result: RepoRunResult) -> None:
        super().record_repo(run_id, result)
        if result.repo.repo_id in self._repo_ids:
            self._proposals.save(proposal_for(result, self._clones))


def proposal_for(result: RepoRunResult, clones: Path) -> FixProposal:
    """A pending proposal on the repo's checked commit, with a one-line diff."""
    repo_id = result.repo.repo_id
    return FixProposal(
        proposal_id=f"{result.repo_run_id}/p",
        repo_run_id=result.repo_run_id,
        repo=result.repo,
        branch_name="hw-review/hw01-2026-11-01",
        base_sha=result.commit_sha or DEFAULT_SHA,
        head_sha="cd" * 20,
        local_repo_dir=clones / repo_id,
        pr_title="HW1 review: small fixes and questions",
        pr_body=f"Review for {repo_id}.",
        diff=f"--- a/{repo_id}.cs\n+++ b/{repo_id}.cs\n",
        verified=True,
    )


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
    outcomes: Mapping[str, Outcome] = field(default_factory=dict)
    """Check outcomes by requirement id, on top of hw01-build being not evaluated."""
    partners: Mapping[str, str] = field(default_factory=dict)
    """The partner each roster row names, by the row's name."""
    report_writer: FakeReportWriter = field(default_factory=FakeReportWriter)
    code_host: FakeCodeHost = field(default_factory=FakeCodeHost)
    propose: tuple[str, ...] = ()
    """Repo ids that get a pending fix proposal when their result is recorded."""
    fixer: FakeLLMClient | None = None
    """Turns the fixer on, with this LLM; every student's repo is the synthetic fixer repo."""
    fixer_overrides: Mapping[str, Any] = field(default_factory=dict)
    """Changes to the fixer's configuration (see `tests.unit.services.fixer_data.config`)."""

    def __post_init__(self) -> None:
        self.runner = self.runner or FakeCodeRunner(results=self.results)
        self.source = self.source or FakeAssignmentSource(self.catalog)
        sources = {url(name): self._fixer_repo() for name in self.names} if self.fixer else {}
        self.git = FakeGitClient(sources=sources, errors=self.git_errors)
        self.checker = FakeRequirementChecker(
            {"hw01-build": NotEvaluatedReason.BUILD_FAILED} | dict(self.outcomes)
        )
        for repo_id in REPO_IDS:
            self.code_host.heads.setdefault(repo_id, BranchHead(name="main", sha=DEFAULT_SHA))

    def services(self, **overrides: Any) -> GraphServices:
        assert self.runner is not None and self.source is not None
        rows = [
            RosterRow(
                row_number=index + 2,
                name=student_name(name),
                github_url=url(name),
                partner_name=self.partners.get(name),
            )
            for index, name in enumerate(self.names)
        ]
        execution = ExecutionService(self.runner, self.tmp_path / "runs", Path("logs"))
        checks = DeterministicCheckService(self.checker)
        values: dict[str, Any] = {
            "roster": RosterService(FakeRosterReader(rows)),
            "assignments": AssignmentCatalogService(self.source, FakeCheckCatalog()),
            "fetch": RepoFetchService(self.git, self.tmp_path / "clones"),
            "execution": execution,
            "checks": checks,
            "run_records": (
                ProposingRecords(self.storage, self.propose, self.tmp_path / "clones")
                if self.propose
                else RunRecordService(self.storage.runs, FakeClock())
            ),
            "reports": ReportService(self.report_writer),
            "publishing": PublishService(
                self.storage.proposals,
                self.code_host,
                self.git,
                self.report_writer,
                FakeClock(),
                commit_author_configured=True,
            ),
            "fixing": self._fixing(execution, checks),
        }
        return GraphServices(**(values | overrides))

    def _fixing(
        self, execution: ExecutionService, checks: DeterministicCheckService
    ) -> FixService | None:
        if self.fixer is None:
            return None
        from tests.unit.services.fixer_data import COURSE, config

        return FixService(
            self.fixer,
            self.git,
            LocalSourceFiles(),
            JinjaTextTemplates(TEMPLATES_DIR),
            self.storage.proposals,
            self.report_writer,
            FakeClock(),
            execution,
            checks,
            self.tmp_path / "runs",
            config(**self.fixer_overrides),
            COURSE,
        )

    def _fixer_repo(self) -> Path:
        from tests.unit.services.fixer_data import FILES

        root = self.tmp_path / "fixer-repo"
        for path, text in FILES.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(text.encode("utf-8"))
        return root

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
