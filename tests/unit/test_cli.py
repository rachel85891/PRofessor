import io
import re
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hw_checker import cli
from hw_checker.bootstrap import open_checkpointer
from hw_checker.config.settings import LLM_ROLES, LlmSettings, Settings
from hw_checker.domain import (
    CheckedCommit,
    CommitChoice,
    PendingReview,
    RepoRunStatus,
    RepoSummary,
    ReviewPrompt,
    RosterWarning,
    RosterWarningKind,
    RunStatus,
    RunSummary,
    UnknownCondition,
)
from hw_checker.domain.errors import LLMError, PublishError, RepoUnavailableError
from hw_checker.graph import RoundGraph, build_round_graph
from tests.fakes.code_host import FakeCodeHost
from tests.fakes.llm import FakeLLMClient
from tests.unit.graph.harness import CATALOG, REPO_IDS, Harness, url


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
        "Round error: c__api: record_repo: disk full",
        "Run id: 20261029-090000-abc123",
    ]


def test_print_summary_ends_with_the_round_report_path() -> None:
    summary = RunSummary(
        run_id="r",
        assignment_id="hw01",
        status=RunStatus.COMPLETED,
        round_report=Path("runs/r/reports/round.md"),
    )
    out = io.StringIO()

    cli.print_summary(summary, [], [], out)

    assert out.getvalue().splitlines()[-1] == f"Round report: {Path('runs/r/reports/round.md')}"


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
    return install(harness, tmp_path, monkeypatch)


def install(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Make the CLI use `harness`'s services and a checkpointer under tmp_path."""
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

    def build(settings: Settings, checkpointer: Any, fixer_llm: Any = None) -> RoundGraph:
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
    report = Path("reports", run_id.group(1), "round.md")
    assert out.splitlines()[-1] == f"Round report: {report}"
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


AVIGAIL, BATYA, CHANA = REPO_IDS


@pytest.fixture
def gated_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[Harness, str]:
    """A round run from the CLI whose three repos wait at the approval gate."""
    harness = install(Harness(tmp_path, propose=REPO_IDS), tmp_path, monkeypatch)
    cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx"])
    out = capsys.readouterr().out
    run_id = re.search(r"^Run id: (\S+)$", out, re.MULTILINE)
    assert run_id is not None
    assert out.splitlines()[-1] == (
        f"Waiting for approval: 3 repos. See them with: python -m hw_checker review "
        f"{run_id.group(1)}"
    )
    return harness, run_id.group(1)


def test_parser_reads_the_approve_command() -> None:
    args = cli.build_parser().parse_args(
        [
            *("approve", "run-1", "--repo", "a__b", "--repo", "c__d", "--decline", "e__f"),
            *("--note", "a__b=Looks good = ready", "--note", "e__f="),
        ]
    )

    assert (args.command, args.run_id) == ("approve", "run-1")
    assert (args.approve, args.decline) == (["a__b", "c__d"], ["e__f"])
    assert args.note == [("a__b", "Looks good = ready"), ("e__f", "")]


@pytest.mark.parametrize("note", ["no-separator", "=text"])
def test_parser_rejects_a_note_without_a_repo(note: str) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["approve", "run-1", "--note", note])


def test_review_lists_the_repos_waiting_at_the_gate(
    gated_round: tuple[Harness, str], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run_id = gated_round

    code = cli.main(["review", run_id])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert out.startswith(f"Round {run_id}: 3 repos waiting for approval\n")
    assert f"  {AVIGAIL}  (Avigail Test)\n" in out
    assert "    branch: hw-review/hw01-2026-11-01\n" in out
    assert "    pull request: HW1 review: small fixes and questions\n" in out
    assert f"    diff: {Path('reports', run_id, f'{AVIGAIL}.diff')}\n" in out
    assert out.splitlines()[-1].startswith(f"Approve with: python -m hw_checker approve {run_id}")


def test_approve_resumes_the_round_publishes_and_shows_what_is_left(
    gated_round: tuple[Harness, str], capsys: pytest.CaptureFixture[str]
) -> None:
    harness, run_id = gated_round

    code = cli.main(["approve", run_id, "--repo", AVIGAIL, "--decline", BATYA])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert re.search(
        rf"^  {AVIGAIL}  published  https://github\.test/avigail/api/pull/1$", out, re.M
    )
    assert out.splitlines()[-1] == (
        f"Waiting for approval: 1 repos. See them with: python -m hw_checker review {run_id}"
    )
    assert [pull.repo_id for pull in harness.code_host.pulls] == [AVIGAIL]

    code = cli.main(["approve", run_id, "--repo", CHANA])

    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert f"  {CHANA}  published" in out
    assert AVIGAIL not in out
    assert out.splitlines()[-1] == "No repos are waiting for approval; the round is done."
    assert cli.main(["review", run_id]) == cli.EXIT_FAILED
    assert capsys.readouterr().out == f"Round {run_id} is not waiting for approval.\n"


def test_approve_shows_why_an_answer_was_not_applied(
    gated_round: tuple[Harness, str], capsys: pytest.CaptureFixture[str]
) -> None:
    harness, run_id = gated_round

    cli.main(["approve", run_id, "--repo", "nobody__api"])

    out = capsys.readouterr().out
    assert "Approval error: Nothing was recorded; not pending: nobody__api." in out
    assert out.splitlines()[-1].startswith("Waiting for approval: 3 repos.")
    assert harness.code_host.pulls == []


def test_approve_fails_when_a_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code_host = FakeCodeHost(errors={AVIGAIL: PublishError("GitHub is down")})
    install(Harness(tmp_path, propose=(AVIGAIL,), code_host=code_host), tmp_path, monkeypatch)
    cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx"])
    run_id = re.search(r"^Run id: (\S+)$", capsys.readouterr().out, re.MULTILINE)
    assert run_id is not None

    code = cli.main(["approve", run_id.group(1), "--repo", AVIGAIL])

    out = capsys.readouterr().out
    assert code == cli.EXIT_FAILED
    assert f"  {AVIGAIL}  failed     GitHub is down" in out


def test_approve_needs_a_decision(
    gated_round: tuple[Harness, str], capsys: pytest.CaptureFixture[str]
) -> None:
    _, run_id = gated_round

    assert cli.main(["approve", run_id]) == cli.EXIT_FAILED
    assert "Nothing to do" in capsys.readouterr().out


def test_approve_refuses_a_round_that_is_not_waiting(
    fake_round: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["approve", "no-such-run", "--repo", AVIGAIL]) == cli.EXIT_FAILED
    assert capsys.readouterr().out == "Round no-such-run is not waiting for approval.\n"


def test_checkpoints_stay_out_of_the_business_database(fake_round: Harness, tmp_path: Path) -> None:
    cli.main(["round", "--assignment", "hw01", "--roster", "r.xlsx", "--limit", "1"])

    path = tmp_path / "workspace" / "checkpoints.sqlite"
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master")}
    assert "checkpoints" in tables and "runs" not in tables


class Clock:
    """Moves 0.5 s on every reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.5
        return self.now


Opener = Callable[[Settings], AbstractContextManager[FakeLLMClient]]


def pong_client(errors: dict[str, LLMError] | None = None) -> FakeLLMClient:
    replies = {str(role): [cli.LlmCheckReply(answer="pong")] for role in LLM_ROLES}
    return FakeLLMClient(replies, errors)


def test_parser_reads_the_llm_check_command() -> None:
    assert cli.build_parser().parse_args(["llm-check"]).command == "llm-check"


def opener(client: FakeLLMClient, opened: list[Settings] | None = None) -> Opener:
    def open_client(settings: Settings) -> AbstractContextManager[FakeLLMClient]:
        if opened is not None:
            opened.append(settings)
        return nullcontext(client)

    return open_client


def test_llm_check_prints_the_provider_each_role_and_model_and_succeeds() -> None:
    client = pong_client()
    settings = Settings()
    out = io.StringIO()

    code = cli.llm_check(settings, out, open_client=opener(client), clock=Clock())

    assert code == cli.EXIT_OK
    lines = out.getvalue().splitlines()
    assert lines[0] == f"LLM provider: openrouter ({settings.llm.base_url})"
    width = max(len(model) for model in settings.llm.roles.values())
    assert lines[1:] == [
        f"  {role:<8}  {settings.llm.roles[role]:<{width}}    0.5 s  ok" for role in LLM_ROLES
    ]
    assert [call.role for call in client.calls] == list(LLM_ROLES)
    assert {call.prompt for call in client.calls} == {cli.LLM_CHECK_PROMPT}


def test_llm_check_with_copilot_prints_the_provider_and_the_copilot_models() -> None:
    client = pong_client()
    opened: list[Settings] = []
    settings = Settings(llm=LlmSettings(provider="copilot"))
    out = io.StringIO()

    code = cli.llm_check(settings, out, open_client=opener(client, opened), clock=Clock())

    assert code == cli.EXIT_OK
    assert opened == [settings]
    lines = out.getvalue().splitlines()
    assert lines[0] == "LLM provider: copilot (the GitHub Copilot seat, through the Copilot SDK)"
    copilot_roles = settings.llm.copilot.roles
    assert [line.split()[:2] for line in lines[1:]] == [
        [role, copilot_roles[role]] for role in LLM_ROLES
    ]


def test_llm_check_reports_a_failing_role_and_fails() -> None:
    client = pong_client({"judge": LLMError("The LLM endpoint's rate limit was reached.")})
    out = io.StringIO()

    code = cli.llm_check(Settings(), out, open_client=opener(client), clock=Clock())

    assert code == cli.EXIT_FAILED
    [judge] = [line for line in out.getvalue().splitlines() if line.startswith("  judge")]
    assert judge.endswith("error: The LLM endpoint's rate limit was reached.")
    assert out.getvalue().count("  ok") == len(LLM_ROLES) - 1


def test_llm_check_flags_a_wrong_answer() -> None:
    replies = {str(role): [cli.LlmCheckReply(answer="hello")] for role in LLM_ROLES}
    out = io.StringIO()

    code = cli.llm_check(Settings(), out, open_client=opener(FakeLLMClient(replies)))

    assert code == cli.EXIT_FAILED
    assert "error: the model did not answer as asked" in out.getvalue()


def test_llm_check_without_a_key_says_how_to_set_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "HW_CHECKER_LLM__API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(llm=LlmSettings()))
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)

    code = cli.main(["llm-check"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED
    assert captured.err.startswith("Error: The LLM API key is not set.")
    assert "HW_CHECKER_LLM__API_KEY=<your key>" in captured.err
    assert captured.out == ""


def test_the_gate_says_when_the_pull_request_is_based_on_an_earlier_commit() -> None:
    checked = CheckedCommit(
        sha="a" * 40,
        authored_at=datetime(2026, 11, 1, tzinfo=UTC),
        choice=CommitChoice.BEFORE_NEXT_TASK,
        next_task_sha="b" * 40,
        later_commits=1,
        latest_sha="c" * 40,
    )
    review = PendingReview(
        repo_id="dana__api",
        repo_run_id="run-1/dana__api",
        student="Dana Test",
        branch_name="hw-review/hw01-2026-11-05",
        pr_title="HW1 review: small fixes",
        checked_commit=checked,
    )
    out = io.StringIO()

    cli.print_prompt(ReviewPrompt(run_id="run-1", pending=(review,), how_to_answer="answer"), out)

    assert (
        "    checked commit: `aaaaaaa` (2026-11-01), the last before the next task started at "
        "`bbbbbbb`. The repo has 1 later commit; the pull request is based on this older "
        "commit.\n"
    ) in out.getvalue()


def test_the_gate_lists_first_the_repos_whose_path_is_unknown() -> None:
    review = PendingReview(
        repo_id="dana__api",
        repo_run_id="run-1/dana__api",
        student="Dana Test",
        branch_name="hw-review/hw01-2026-11-05",
        pr_title="HW1 review: small fixes",
    )
    unknown = UnknownCondition(
        repo_id="dana__api", assignment_id="hw03", detail="applies_when x could not be evaluated"
    )
    out = io.StringIO()

    cli.print_prompt(
        ReviewPrompt(
            run_id="run-1", unknown_conditions=(unknown,), pending=(review,), how_to_answer="a"
        ),
        out,
    )

    assert out.getvalue().startswith(
        "Review first: whether an assignment applies could not be decided in\n"
        "  dana__api: hw03 (applies_when x could not be evaluated)\n"
        "Round run-1: 1 repos waiting for approval\n"
    )
