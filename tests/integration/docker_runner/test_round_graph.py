"""The round graph end to end: real Docker runner, real checks, committed assignments.

Git is faked by copying the sample solution, and the roster holds one invented student.
"""

from pathlib import Path

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT
from hw_checker.domain import RepoRunStatus, RosterRow, RunStatus, StepStatus
from hw_checker.graph import GraphServices, RoundInput, build_round_graph
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.repo_fetch import RepoFetchService
from hw_checker.services.roster import RosterService
from hw_checker.services.run_record import RunRecordService
from tests.fakes.clock import FakeClock
from tests.fakes.git import FakeGitClient
from tests.fakes.roster import FakeRosterReader
from tests.integration.docker_runner.conftest import FIXTURE

pytestmark = pytest.mark.integration

STUDENT_URL = "https://github.com/test-student/webapi"
REPO_ID = "test-student__webapi"


def test_round_graph_checks_the_sample_solution_end_to_end(
    docker_runner: DockerCodeRunner, tmp_path: Path
) -> None:
    database_url = f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}"
    migrate(database_url)
    storage = SqlStorage(database_url)
    registry = CheckRegistry(builtin_checks())
    catalog = AssignmentCatalogService(YamlAssignmentSource(REPO_ROOT / "assignments"), registry)
    roster = FakeRosterReader(
        [RosterRow(row_number=2, name="Test Student", github_url=STUDENT_URL)]
    )
    services = GraphServices(
        roster=RosterService(roster),
        assignments=catalog,
        fetch=RepoFetchService(FakeGitClient(sources={STUDENT_URL: FIXTURE}), tmp_path / "clones"),
        execution=ExecutionService(docker_runner, tmp_path / "runs", Path("logs")),
        checks=DeterministicCheckService(DeterministicRequirementChecker(registry)),
        run_records=RunRecordService(storage.runs, FakeClock()),
    )
    graph = build_round_graph(catalog.list_assignments(), services, max_parallel_repos=1)

    output = graph.invoke(
        RoundInput(assignment_id="hw01", roster_path="roster.xlsx", run_id="it-round")
    )

    run = storage.runs.get("it-round")
    storage.close()
    [repo_run] = run.repo_runs
    assert repo_run.errors == ()
    assert repo_run.status is RepoRunStatus.DONE
    assert repo_run.execution is not None
    assert repo_run.execution.build_status is StepStatus.SUCCEEDED
    assert [r.assignment_id for r in repo_run.assignment_results] == ["_general", "hw01"]
    for result in repo_run.assignment_results:
        assert result.findings == (), result.findings
        assert result.not_evaluated == (), result.not_evaluated
    assert run.status is RunStatus.COMPLETED
    assert output["summary"].repos[0].repo_id == REPO_ID
    assert (tmp_path / "runs" / "it-round" / "logs" / REPO_ID).is_dir()
