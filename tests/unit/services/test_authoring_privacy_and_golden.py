"""The author never sees a student's code (ADR 0023), and its prompt for a synthetic
intake is a golden file (stage 16): a change to it is the developer's to accept."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from hw_checker.checks.registry import CheckResult, check
from hw_checker.domain import CheckInput
from hw_checker.graph import AuthoringInput, build_authoring_graph
from tests.fakes.llm import FakeLLMClient
from tests.unit.services.authoring_data import build_service, make_repo, valid_reply, write

GOLDEN = Path(__file__).parent / "golden" / "authoring" / "author_prompt.md"
STUDENT_MARK = "STUDENT_CODE_7f3a"


def test_the_author_never_sees_a_file_of_the_workspace_clones(tmp_path: Path) -> None:
    clone = make_repo(tmp_path / "workspace" / "clones" / "dana__users-api", 4)
    write(clone / "Controllers" / "UsersController.cs", f"class {STUDENT_MARK} {{}}\n")
    write(clone / "README.md", f"{STUDENT_MARK} by Dana\n")
    llm = FakeLLMClient({"author": [valid_reply()]})
    graph = build_authoring_graph(build_service(tmp_path, llm), 3, 2)

    graph.invoke(AuthoringInput(assignment_id="hw02", with_execution=True))

    assert llm.calls, "the author was asked"
    for call in llm.calls:
        assert STUDENT_MARK not in call.prompt
        assert "dana" not in call.prompt.lower()
        assert "UsersController" not in call.prompt
    report = next((tmp_path / "workspace" / "authoring" / "hw02").glob("*.md"))
    assert STUDENT_MARK not in report.read_text(encoding="utf-8")
    assert "dana" not in report.read_text(encoding="utf-8").lower()


class _MinParams(BaseModel):
    """`min`: at least this many."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min: int


@check("golden_projects", _MinParams, "At least min projects (a check of this test).")
def _projects(check_input: CheckInput, params: _MinParams) -> CheckResult:
    return ()


@check("golden_names", _MinParams, "Names are long enough (a check of this test).")
def _names(check_input: CheckInput, params: _MinParams) -> CheckResult:
    return ()


def test_the_author_prompt_for_a_synthetic_intake_is_the_golden_one(tmp_path: Path) -> None:
    service = build_service(tmp_path, only_checks=(_projects, _names))

    prompt = service.author_prompt(service.load_intake("hw02"))

    assert prompt == GOLDEN.read_bytes().decode("utf-8"), (
        f"The author's prompt changed; if the change is meant, the developer accepts it by "
        f"writing the new prompt to {GOLDEN}."
    )
