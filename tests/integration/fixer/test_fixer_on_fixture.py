"""The real fixer, once, on a synthetic fixture: a real LLM, real Docker and real git.

Opt-in: skipped unless Docker answers and, with the OpenRouter provider, unless
HW_CHECKER_LLM__API_KEY is set (the Copilot provider uses the Copilot CLI's login).

The fixture (`sample_webapi_no_final_newline`) is written for the tests and holds no
student code or names. The test runs the `fix_repo` subgraph on a git copy of it and prints
the proposal's diff, the PR text and the tokens of each LLM role.
"""

import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.adapters.docker_runner.engine import EngineError, SdkDockerEngine
from hw_checker.adapters.docker_runner.runner import DockerCodeRunner
from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.jinja_templates import JinjaTextTemplates
from hw_checker.adapters.local_files import LocalSourceFiles
from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.adapters.system_clock import SystemClock
from hw_checker.bootstrap import fixer_config, open_llm_client
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.config.settings import REPO_ROOT, load_settings
from hw_checker.domain import (
    FetchedRepo,
    RepoRunResult,
    RepoRunStatus,
    RepoTarget,
    Run,
    RunStatus,
    Student,
)
from hw_checker.graph.fix import FixInput, build_fix_graph
from hw_checker.prompts import TEMPLATES_DIR
from hw_checker.reporting.writer import MarkdownReportWriter
from hw_checker.services.assignments import AssignmentCatalogService
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.fixing import FixService
from tests.integration.docker_runner.conftest import FIXTURES, runner_config

pytestmark = pytest.mark.integration

FIXTURE = FIXTURES / "sample_webapi_no_final_newline"
BROKEN_BY_THE_DATA_FILE = "hw01-read-back"
URL = "https://github.com/test-student/webapi"
REPO = RepoTarget(
    repo_id="test-student__webapi",
    owner="test-student",
    name="webapi",
    url=URL,
    student=Student(name="Test Student", github_url=URL),
    pair_id="test-student__webapi",
)
RUN_ID = "it-fixer"
AUTHOR = ("HW Checker Test", "hw-checker-test@example.invalid")


@pytest.fixture
def runner() -> Iterator[DockerCodeRunner]:
    try:
        engine = SdkDockerEngine.from_environment(timeout_seconds=120)
        engine.ping()
    except EngineError as error:
        pytest.skip(f"Docker is not available: {error}")
    docker_runner = DockerCodeRunner(runner_config(), engine)
    docker_runner.sweep_leftovers()
    docker_runner.check_environment()
    yield docker_runner
    docker_runner.sweep_leftovers()


def checked_copy(tmp_path: Path) -> tuple[Path, str]:
    """The fixture as a git working copy with one commit, like a fetched student repo."""
    copy = tmp_path / "clones" / REPO.repo_id
    shutil.copytree(FIXTURE, copy, ignore=shutil.ignore_patterns("bin", "obj"))
    name, email = AUTHOR
    for args in (
        ["init", "--quiet", "-b", "main"],
        ["add", "--all"],
        ["-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet", "-m", "hw1"],
    ):
        subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=copy, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=copy, check=True, capture_output=True, text=True
    ).stdout.strip()
    return copy, sha


def test_the_fixer_proposes_a_verified_review_branch_that_fixes_the_data_file(
    runner: DockerCodeRunner, tmp_path: Path
) -> None:
    settings = load_settings()
    if settings.llm.provider == "openrouter" and settings.llm.api_key is None:
        pytest.skip("HW_CHECKER_LLM__API_KEY is not set")
    copy, sha = checked_copy(tmp_path)
    registry = CheckRegistry(builtin_checks())
    catalog = AssignmentCatalogService(
        YamlAssignmentSource(REPO_ROOT / "assignments", REPO_ROOT / "course"), registry
    )
    assignments = catalog.select_for_round("hw01")
    execution_service = ExecutionService(runner, tmp_path / "runs", Path("logs"))
    checks = DeterministicCheckService(DeterministicRequirementChecker(registry))
    execution = execution_service.execute(RUN_ID, REPO.repo_id, copy, assignments)
    results = [checks.evaluate(assignment, copy, execution) for assignment in assignments]
    url = f"sqlite:///{(tmp_path / 'hw.db').as_posix()}"
    migrate(url)
    storage = SqlStorage(url)
    storage.runs.save(
        Run(
            run_id=RUN_ID,
            assignment_id="hw01",
            started_at=datetime.now(UTC),
            status=RunStatus.RUNNING,
            repo_runs=(
                RepoRunResult(
                    repo_run_id=f"{RUN_ID}/{REPO.repo_id}",
                    repo=REPO,
                    commit_sha=sha,
                    status=RepoRunStatus.DONE,
                    execution=execution,
                    assignment_results=tuple(results),
                ),
            ),
        )
    )
    failing = sorted({f.requirement_id for r in results for f in r.findings})

    with open_llm_client(settings) as llm:
        fixing = FixService(
            llm,
            LocalGitClient("git", 120, *AUTHOR),
            LocalSourceFiles(),
            JinjaTextTemplates(TEMPLATES_DIR),
            storage.proposals,
            MarkdownReportWriter(tmp_path / "runs"),
            SystemClock(),
            execution_service,
            checks,
            tmp_path / "runs",
            fixer_config(settings),
            YamlAssignmentSource(REPO_ROOT / "assignments", REPO_ROOT / "course").load_course(),
        )
        graph = build_fix_graph(catalog.list_assignments(), fixing)
        output: dict[str, Any] = graph.invoke(
            FixInput(
                run_id=RUN_ID,
                repo=REPO,
                selected_assignment_ids=[assignment.id for assignment in assignments],
                fetched=FetchedRepo(working_copy=copy, commit_sha=sha),
                execution=execution,
                assignment_results=results,
            )
        )

    proposals = storage.proposals.list_for_run(RUN_ID)
    storage.close()
    [outcome] = output["fix_outcomes"]
    print(f"\nFailing before: {', '.join(failing)}")
    print(f"Outcome: {outcome.model_dump_json()}")
    for usage in output["llm_usage"]:
        print(f"LLM {usage.role}: {usage.input_tokens} input, {usage.output_tokens} output")
    for proposal in proposals:
        print(f"Title: {proposal.pr_title}\n\nBody:\n{proposal.pr_body}\nDiff:\n{proposal.diff}")
    assert BROKEN_BY_THE_DATA_FILE in failing
    assert output["round_errors"] == []
    assert outcome.proposal_id is not None, outcome.reason
    [proposal] = proposals
    assert proposal.verified
    # The final newline clears a critical requirement, so it is a real fix (§4).
    assert proposal.pr_title == "HW1 review: fixes and questions"
    assert proposal.pr_body.startswith("Hi! ")
    fixed = proposal.local_repo_dir
    recheck = execution_service.execute(RUN_ID, f"{REPO.repo_id}--after", fixed, assignments)
    after = {
        f.requirement_id for a in assignments for f in checks.evaluate(a, fixed, recheck).findings
    }
    print(f"Failing after: {', '.join(sorted(after))}")
    assert BROKEN_BY_THE_DATA_FILE not in after
