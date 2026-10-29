import io
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from hw_checker import cli
from hw_checker.bootstrap import open_checkpointer
from hw_checker.config.settings import Settings
from hw_checker.domain import (
    RepoRunStatus,
    RepoSummary,
    RosterWarning,
    RosterWarningKind,
    RunStatus,
    RunSummary,
)
from hw_checker.domain.errors import RepoUnavailableError
from hw_checker.graph import RoundGraph, build_round_graph
from tests.unit.graph.harness import CATALOG, Harness, url


def test_parser_reads_the_round_command() -> None:
    args = cli.build_parser().parse_args(
        [
            *("round", "--assignment", "hw01", "--roster", "r.xlsx"),
            *("--repo", "a__b", "--repo", "c__d", "--limit", "3"),
        ]
    )

    assert (args.command, args.assignment, args.roster) == ("round", "hw01", "r.xlsx")
    assert (args.repo_ids, args.limit) == (["a__b", "c__d"], 3)


def test_parser_defaults_to_every_repo() -> None:
    args = cli.build_parser().parse_args(["round", "--assignment", "hw01", "--roster", "r.xlsx"])

    assert (args.repo_ids, args.limit) == ([], None)


@pytest.mark.parametrize(
    "argv",
    [
        ["round", "--roster", "r.xlsx"],
        ["round", "--assignment", "hw01", "--roster", "r.xlsx", "--limit", "0"],
        ["round", "--assignment", "hw01", "--roster", "r.xlsx", "--limit", "x"],
        [],
    ],
    ids=["no-assignment", "zero-limit", "text-limit", "no-command"],
)
def test_parser_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(argv)


def test_print_summary_lists_repos_warnings_errors_and_the_run_id() -> None:
    summary = RunSummary(
        run_id="20261029-090000-abc123",
        assignment_id="hw01",
        status=RunStatus.COMPLETED,
        repos=(
            RepoSummary(
                repo_id="avigail__api", status=RepoRunStatus.DONE, major=2, not_evaluated=1
            ),
            RepoSummary(
                repo_id="b__api",
                status=RepoRunStatus.FAILED,
                errors=("fetch_repo: repository not found",),
            ),
        ),
    )
    warning = RosterWarning(kind=RosterWarningKind.MISSING_PARTNER, message="Row 3: no partner")
    out = io.StringIO()

    cli.print_summary(summary, [warning], ["c__api: record_repo: disk full"], out)

    assert out.getvalue().splitlines() == [
        "Round for hw01: completed",
        "  avigail__api  done    critical 0  major 2  minor 0  not evaluated 1",
        "  b__api        failed  critical 0  major 0  minor 0  not evaluated 0",
        "                  error: fetch_repo: repository not found",
        "Roster warnings (1):",
        "  - Row 3: no partner",
        "Not saved: c__api: record_repo: disk full",
        "Run id: 20261029-090000-abc123",
    ]


def test_print_summary_says_when_no_repo_was_checked() -> None:
    out = io.StringIO()

    cli.print_summary(
        RunSummary(run_id="r", assignment_id="hw01", status=RunStatus.COMPLETED), [], [], out
    )

    assert "No repos were checked." in out.getvalue()


@pytest.fixture
def fake_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """The CLI over fake services, with settings pointing at tmp_path."""
    harness = Harness(tmp_path, git_errors={url("batya"): RepoUnavailableError("not found")})
    settings = Settings(workspace_dir=tmp_path / "workspace")
    for name in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    def build(settings: Settings, checkpointer: Any) -> RoundGraph:
        return build_round_graph(CATALOG, harness.services(), 2, checkpointer)

    monkeypatch.setattr(cli, "build_round_graph_from_settings", build)
    return harness


def test_main_runs_a_round_and_prints_its_summary(
    fake_round: Harness, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx", "--limit", "2"])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert out.startswith("Round for hw01: completed\n")
    assert re.search(r"avigail__api\s+done\s+critical 0", out)
    assert re.search(r"batya__api\s+failed", out)
    assert "error: fetch_repo: not found" in out
    assert "chana__api" not in out
    run_id = re.search(r"^Run id: (\S+)$", out, re.MULTILINE)
    assert run_id is not None
    settings = Settings(workspace_dir=tmp_path / "workspace")
    with open_checkpointer(settings) as checkpointer:
        saved = checkpointer.get_tuple({"configurable": {"thread_id": run_id.group(1)}})
    assert saved is not None
    assert fake_round.storage.runs.get(run_id.group(1)).status is RunStatus.COMPLETED


def test_main_returns_failed_when_no_repo_could_be_checked(
    fake_round: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx", "--repo", "batya__api"])

    assert code == cli.EXIT_FAILED
    assert capsys.readouterr().out.startswith("Round for hw01: failed\n")


def test_main_reports_a_round_that_cannot_start(
    fake_round: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["round", "--assignment", "hw09", "--roster", "r.xlsx"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert captured.err.startswith("Error: Unknown assignment 'hw09'")
    assert captured.out == ""


def test_main_refuses_to_run_with_tracing_on(
    fake_round: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    code = cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx"])

    assert code == cli.EXIT_FAILED
    assert "tracing must stay off" in capsys.readouterr().err


def test_checkpoints_stay_out_of_the_business_database(fake_round: Harness, tmp_path: Path) -> None:
    cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx", "--limit", "1"])

    path = tmp_path / "workspace" / "checkpoints.sqlite"
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master")}
    assert "checkpoints" in tables and "runs" not in tables
