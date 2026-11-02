"""Synthetic data for the fixer's tests: an invented repo, assignment and model replies."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hw_checker.adapters.jinja_templates import JinjaTextTemplates
from hw_checker.adapters.local_files import LocalSourceFiles
from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditKind,
    FetchedRepo,
    FixPlan,
    PlannedEdit,
    PullRequestText,
    RepoRunResult,
    RepoRunStatus,
    RequirementKind,
    Run,
    RunStatus,
    SelfReview,
    Severity,
)
from hw_checker.prompts import TEMPLATES_DIR
from hw_checker.services.deterministic_checks import DeterministicCheckService
from hw_checker.services.execution import ExecutionService
from hw_checker.services.fixing import FixerConfig, FixService
from tests.fakes.checks import FakeRequirementChecker, Outcome
from tests.fakes.clock import FakeClock
from tests.fakes.execution import FakeCodeRunner
from tests.fakes.git import DEFAULT_SHA, FakeGitClient
from tests.fakes.llm import FakeLLMClient
from tests.fakes.reporting import FakeReportWriter
from tests.fakes.storage import InMemoryStorage
from tests.unit.domain import builders

RUN_ID = "run-1"
REPO = builders.repo_target(repo_id="dana__api")
CONTROLLER = "Controllers/UsersController.cs"
MODEL = "Models/User.cs"
DATA = "users.json"
CONTROLLER_TEXT = """using System.Text.Json;

public class UsersController
{
    public string Update(int kind, int count)
    {
        int New_Count = count;
        switch (kind)
        {
            case 1:
                New_Count++;
            case 2:
                New_Count--;
                break;
        }
        return New_Count.ToString();
    }
}
"""
MODEL_TEXT = "public class User\n{\n    public int id { get; set; }\n}\n"
FILES = {
    CONTROLLER: CONTROLLER_TEXT,
    MODEL: MODEL_TEXT,
    "Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n',
    "appsettings.json": "{}\n",
    DATA: '[{"id":1}]',
    "bin/Debug/Api.dll": "binary",
}

ASSIGNMENT = Assignment(
    id="hw01",
    order=1,
    title="Users API",
    requirements=(
        builders.requirement(
            id="hw01-update", title="PUT updates a user", kind=RequirementKind.BUILD
        ),
        builders.requirement(
            id="hw01-naming",
            title="Names follow C# conventions",
            severity=Severity.MINOR,
            kind=RequirementKind.BUILD,
        ),
    ),
)
FINDING = builders.finding(
    id="f-1",
    requirement_id="hw01-update",
    assignment_id="hw01",
    message_en="Updating with kind 1 also runs kind 2.",
    evidence=builders.evidence(file_path=CONTROLLER, line_start=10, line_end=12, quote="case 1:"),
)
RESULTS = (AssignmentResult(assignment_id="hw01", findings=(FINDING,)),)

MISSING_BREAK = "                New_Count++;\n            case 2:\n"
BREAK_FIX = PlannedEdit(
    file=CONTROLLER,
    find=MISSING_BREAK,
    replace=(
        "                New_Count++;\n"
        "                //fix: added the missing break, so kind 1 no longer runs kind 2\n"
        "                break;\n"
        "            case 2:\n"
    ),
    kind=EditKind.FIX,
    finding_ids=("hw01-update",),
    reason="case 1 falls through to case 2.",
    fallback_note="Does case 1 need a break? Right now it also runs case 2.",
)
NAMING_NOTE = PlannedEdit(
    file=CONTROLLER,
    find="        int New_Count = count;\n",
    replace=(
        "        // Local variables are camelCase in C#: newCount.\n"
        "        int New_Count = count;\n"
    ),
    kind=EditKind.NOTE,
    finding_ids=(),
    reason="Naming: locals are camelCase.",
    fallback_note=None,
)
OUTSIDE = PlannedEdit(
    file=DATA,
    find="[",
    replace="//fix: x\n[",
    kind=EditKind.FIX,
    finding_ids=(),
    reason="Not allowed: the data file is not code.",
    fallback_note=None,
)
PLAN = FixPlan(edits=(BREAK_FIX, NAMING_NOTE), files_notes=())
NO_VIOLATIONS = SelfReview(violations=())
PR_TEXT = PullRequestText(
    opening="Your API builds, and creating and reading users works.",
    fixed=("Updating with kind 1 also ran kind 2; a break was missing, and now it is there.",),
    for_you=(),
    names=("Local variables are camelCase in C#: newCount instead of New_Count.",),
    files=(),
)


def config(**overrides: Any) -> FixerConfig:
    values: dict[str, Any] = {
        "max_attempts": 2,
        "max_edits": 12,
        "max_fix_lines": 6,
        "max_file_chars": 12_000,
        "max_prompt_chars": 60_000,
        "self_review": True,
        "source_suffixes": (".cs", ".csproj"),
        "config_globs": ("appsettings*.json",),
        "data_suffixes": (".txt", ".json"),
        "ignored_globs": ("launchSettings.json",),
        "skip_dirs": ("bin", "obj"),
        "reply_attempts": 2,
        "cache_salt": "test",
    }
    return FixerConfig(**(values | overrides))


class FixerSetup:
    """A checked working copy on disk, a stored run, and the fix service over fakes."""

    def __init__(
        self,
        tmp_path: Path,
        replies: dict[str, list[BaseModel]] | None = None,
        runner: FakeCodeRunner | None = None,
        outcomes: dict[str, Outcome] | None = None,
        **config_overrides: Any,
    ) -> None:
        self.checked = tmp_path / "clones" / REPO.repo_id
        for path, text in FILES.items():
            (self.checked / path).parent.mkdir(parents=True, exist_ok=True)
            (self.checked / path).write_bytes(text.encode("utf-8"))
        self.fetched = FetchedRepo(working_copy=self.checked, commit_sha=DEFAULT_SHA)
        self.storage = InMemoryStorage()
        self.storage.runs.save(
            Run(
                run_id=RUN_ID,
                assignment_id="hw01",
                started_at=FakeClock().now(),
                status=RunStatus.RUNNING,
                repo_runs=(
                    RepoRunResult(
                        repo_run_id=f"{RUN_ID}/{REPO.repo_id}",
                        repo=REPO,
                        commit_sha=DEFAULT_SHA,
                        status=RepoRunStatus.DONE,
                    ),
                ),
            )
        )
        self.llm = FakeLLMClient(
            replies
            if replies is not None
            else {"fixer": [PLAN], "judge": [NO_VIOLATIONS], "writer": [PR_TEXT]}
        )
        self.git = FakeGitClient()
        self.writer = FakeReportWriter()
        self.runs = tmp_path / "runs"
        self.runner = runner or FakeCodeRunner()
        self.checker = FakeRequirementChecker(outcomes)
        self.service = FixService(
            self.llm,
            self.git,
            LocalSourceFiles(),
            JinjaTextTemplates(TEMPLATES_DIR),
            self.storage.proposals,
            self.writer,
            FakeClock(),
            ExecutionService(self.runner, self.runs, Path("logs")),
            DeterministicCheckService(self.checker),
            self.runs,
            config(**config_overrides),
        )

    def fix_copy(self) -> Path:
        return self.runs / RUN_ID / "fixes" / REPO.repo_id

    def text(self, path: str) -> str:
        return (self.fix_copy() / path).read_bytes().decode("utf-8")
