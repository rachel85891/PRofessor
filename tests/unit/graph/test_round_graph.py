"""The round, repo and assignment graphs over fakes for every port."""

import threading
from pathlib import Path
from typing import Any

import pytest

from hw_checker.domain import (
    ExecutionResult,
    FailureKind,
    FindingBrief,
    RepoRunResult,
    RepoRunStatus,
    RunStatus,
    RunSummary,
    Severity,
    StepStatus,
)
from hw_checker.domain.errors import (
    ExecutionEnvironmentError,
    InvalidAssignmentError,
    RepoUnavailableError,
    RosterError,
)
from hw_checker.graph import build_repo_graph
from hw_checker.services.run_record import RunRecordService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.clock import FakeClock
from tests.fakes.execution import FakeCodeRunner
from tests.fakes.reporting import FAKE_REPORTS_DIR, FakeReportWriter
from tests.fakes.storage import InMemoryStorage
from tests.unit.domain import builders
from tests.unit.graph.harness import (
    CATALOG,
    REPO_IDS,
    ExplodingChecks,
    FailingRecords,
    Harness,
    assignment,
    url,
)


def test_repo_graph_has_one_node_per_assignment(tmp_path: Path) -> None:
    graph = build_repo_graph(CATALOG, Harness(tmp_path).services())

    nodes = set(graph.get_graph().nodes)

    assert {node for node in nodes if node.startswith("assignment_")} == {
        "assignment__general",
        "assignment_hw01",
        "assignment_hw02",
    }
    assert {"fetch_repo", "build_and_run", "record_repo", "write_report"} <= nodes


def test_round_graph_nodes_and_parallelism(tmp_path: Path) -> None:
    graph = Harness(tmp_path).graph()

    assert {"start_round", "check_repo", "finish_round", "write_round_report"} <= set(
        graph.get_graph().nodes
    )
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
    reports = FAKE_REPORTS_DIR / "run-1"
    assert output["summary"] == RunSummary.from_run(
        stored, REPO_IDS, {repo_id: reports / f"{repo_id}.md" for repo_id in REPO_IDS}
    ).model_copy(update={"reports_dir": reports, "round_report": reports / "round.md"})
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

    # Repos are saved as they finish, which depends on the parallel scheduling; the
    # summary follows the roster.
    assert sorted(r.repo.repo_id for r in harness.stored().repo_runs) == expected
    assert [line.repo_id for line in output["summary"].repos] == expected


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


class LastFirstRecords(RunRecordService):
    """Holds the first roster repo back until the last one is saved, so it is saved last."""

    def __init__(self, storage: InMemoryStorage) -> None:
        super().__init__(storage.runs, FakeClock())
        self._last_saved = threading.Event()

    def record_repo(self, run_id: str, result: RepoRunResult) -> None:
        if result.repo.repo_id == REPO_IDS[0]:
            assert self._last_saved.wait(5), "the last repo was never saved"
        super().record_repo(run_id, result)
        if result.repo.repo_id == REPO_IDS[-1]:
            self._last_saved.set()


def test_summary_and_round_report_follow_the_roster_not_the_finish_order(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    output = harness.run(harness.graph(run_records=LastFirstRecords(harness.storage)))

    assert harness.stored().repo_runs[-1].repo.repo_id == REPO_IDS[0]
    assert [line.repo_id for line in output["summary"].repos] == list(REPO_IDS)
    [round_report] = harness.report_writer.round_reports
    assert [repo.repo_id for repo in round_report.repos] == list(REPO_IDS)
    assert [line.repo_id for line in round_report.summary.repos] == list(REPO_IDS)


def test_every_repo_gets_a_report_with_the_round_and_the_partner(tmp_path: Path) -> None:
    harness = Harness(tmp_path, partners={"avigail": "Batya Test", "batya": "Avigail Test"})

    output = harness.run()

    reports = {r.repo_run.repo.repo_id: r for r in harness.report_writer.repo_reports}
    assert sorted(reports) == sorted(REPO_IDS)
    assert {r.assignment_id for r in reports.values()} == {"hw01"}
    assert reports["avigail__api"].partner == "Batya Test"
    assert reports["batya__api"].partner == "Avigail Test"
    assert reports["chana__api"].partner is None
    assert set(reports["chana__api"].requirement_titles) == {
        "_general-build",
        "hw01-build",
        "hw01-http",
    }
    [round_report] = harness.report_writer.round_reports
    assert round_report.partners == {"avigail__api": "Batya Test", "batya__api": "Avigail Test"}
    assert output["summary"].round_report == FAKE_REPORTS_DIR / "run-1" / "round.md"


def test_a_report_that_cannot_be_written_is_a_round_error(tmp_path: Path) -> None:
    harness = Harness(tmp_path, report_writer=FakeReportWriter(failing_repos=["batya__api"]))

    output = harness.run()

    assert output["round_errors"] == [
        "batya__api: write_report: Cannot write the report batya__api.md: disk full"
    ]
    assert harness.repo_run("batya__api").status is RepoRunStatus.DONE
    lines = {line.repo_id: line for line in output["summary"].repos}
    assert lines["batya__api"].report_path is None
    assert lines["avigail__api"].report_path == FAKE_REPORTS_DIR / "run-1" / "avigail__api.md"
    assert harness.stored().status is RunStatus.COMPLETED


def test_round_files_that_cannot_be_written_are_a_round_error(tmp_path: Path) -> None:
    harness = Harness(tmp_path, report_writer=FakeReportWriter(round_fails=True))

    output = harness.run()

    assert output["round_errors"] == [
        "write_round_report: Cannot write the report round.md: disk full"
    ]
    assert output["summary"].round_report is None
    assert [line.repo_id for line in output["summary"].repos] == list(REPO_IDS)


def test_summary_lists_each_repos_findings(tmp_path: Path) -> None:
    finding = builders.finding(
        requirement_id="hw01-http",
        assignment_id="hw01",
        severity=Severity.CRITICAL,
        message_en="GET /api/hw01: expected 200, got 500.\nThe app threw.",
    )
    harness = Harness(tmp_path, names=("avigail",), outcomes={"hw01-http": (finding,)})

    output = harness.run()

    [line] = output["summary"].repos
    assert line.findings == (
        FindingBrief(
            requirement_id="hw01-http",
            severity=Severity.CRITICAL,
            message="GET /api/hw01: expected 200, got 500.",
        ),
    )
    assert line.critical == 1
