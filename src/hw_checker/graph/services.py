"""The services the graph's nodes call, passed into the graph builders."""

from dataclasses import dataclass

from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.repo_fetch import RepoFetchService
from hw_checker.services.roster import RosterService
from hw_checker.services.run_record import RunRecordService


@dataclass(frozen=True)
class GraphServices:
    """Every service a round needs; created by bootstrap, or from fakes in tests."""

    roster: RosterService
    assignments: AssignmentCatalogService
    fetch: RepoFetchService
    execution: ExecutionService
    checks: DeterministicCheckService
    run_records: RunRecordService
