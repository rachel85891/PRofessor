"""The `assignment` commands (stage 16): new, then draft until the approval, and approve,
revise or stop it, over the real SQLite checkpointer and a fake author."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from hw_checker import cli
from hw_checker.config.settings import Settings
from hw_checker.graph import AuthoringGraph, build_authoring_graph
from hw_checker.services.authoring import INTAKE_CHANGED, AuthoringService
from tests.fakes.llm import FakeLLMClient
from tests.unit.services.authoring_data import (
    INTAKE,
    build_service,
    capture_reply,
    make_folders,
    valid_reply,
    write,
)


@pytest.fixture
def author(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    """The CLI over the synthetic folders under tmp_path, with a fake author."""
    make_folders(tmp_path)
    settings = Settings(
        workspace_dir=tmp_path / "workspace",
        assignments_dir=tmp_path / "assignments",
        course_dir=tmp_path / "course",
    )
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda settings: None)
    llm = FakeLLMClient({"author": [valid_reply()]})

    def build(settings: Settings, author_llm: Any, checkpointer: Any = None) -> AuthoringGraph:
        return build_authoring_graph(build_service(tmp_path, llm), 3, 2, checkpointer)

    monkeypatch.setattr(cli, "build_authoring_graph_from_settings", build)
    return llm


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(["assignment", *argv])
    return code, capsys.readouterr().out


def test_new_creates_the_folder_and_says_what_next(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "new", "hw03")

    assert code == cli.EXIT_OK
    assert "assignment draft hw03" in out
    intake = yaml.safe_load((tmp_path / "assignments" / "hw03" / "intake.yaml").read_text("utf-8"))
    assert (intake["order"], intake["taught"]) == (3, ["controllers", "layers"])
    assert author.calls == []


def test_draft_waits_then_approve_writes_the_files(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(capsys, "draft", "hw02")
    assert code == cli.EXIT_OK
    assert "The draft of hw02 is valid. Read the report:" in out
    assert "authoring_report.md" in out

    code, out = _run(capsys, "draft", "hw02")
    assert (code, out.strip()) == (
        cli.EXIT_FAILED,
        "hw02 is waiting for your answer: approve, revise or stop it first.",
    )

    code, out = _run(capsys, "approve", "hw02")
    assert code == cli.EXIT_OK
    assert out.startswith("Approved hw02:\n")
    rules = (tmp_path / "assignments" / "hw02" / "review_rules.yaml").read_text("utf-8")
    assert "status: approved" in rules and "status: draft" not in rules

    code, out = _run(capsys, "approve", "hw02")
    assert (code, out.strip()) == (cli.EXIT_FAILED, "hw02 is not waiting for approval.")


def test_revise_drafts_again_and_stop_removes_the_draft(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys, "draft", "hw02")

    code, out = _run(capsys, "revise", "hw02", "--note", "Use 201 only")
    assert code == cli.EXIT_OK
    assert "- Use 201 only" in author.calls[-1].prompt

    code, out = _run(capsys, "stop", "hw02")
    assert (code, out.strip()) == (cli.EXIT_OK, "Stopped hw02; no drafted files are kept.")
    assert not (tmp_path / "assignments" / "hw02" / "requirements.yaml").exists()


def _fail_write_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Draft, edit a drafted file, and let `approve` fail in `write_approved`."""
    _run(capsys, "draft", "hw02")
    requirements_file = tmp_path / "assignments" / "hw02" / "requirements.yaml"
    text = requirements_file.read_text("utf-8")
    requirements_file.write_text(text.replace("severity: major", "severity: minor"), "utf-8")

    def broken(*args: Any) -> Any:
        raise AttributeError("'dict' object has no attribute 'id'")

    with monkeypatch.context() as patch:
        patch.setattr(AuthoringService, "review_edits", broken)
        with pytest.raises(AttributeError):
            _run(capsys, "approve", "hw02")


def test_approve_runs_a_failed_write_approved_again_with_the_edits(
    author: FakeLLMClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    llm = FakeLLMClient({"author": [capture_reply()]})

    def build(settings: Settings, author_llm: Any, checkpointer: Any = None) -> AuthoringGraph:
        return build_authoring_graph(build_service(tmp_path, llm), 3, 2, checkpointer)

    monkeypatch.setattr(cli, "build_authoring_graph_from_settings", build)
    _fail_write_approved(tmp_path, monkeypatch, capsys)

    code, out = _run(capsys, "approve", "hw02")

    assert code == cli.EXIT_OK
    assert out.startswith("Running the step that failed again: write_approved\n")
    assert "Not applied: Your edits were validated" in out
    code, out = _run(capsys, "approve", "hw02")
    assert (code, out.splitlines()[0]) == (cli.EXIT_OK, "Approved hw02:")


def test_revise_after_a_failed_step_says_to_draft_again(
    author: FakeLLMClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fail_write_approved(tmp_path, monkeypatch, capsys)

    code, out = _run(capsys, "revise", "hw02", "--note", "Use 201 only")

    assert (code, out.strip()) == (
        cli.EXIT_FAILED,
        'The last step of hw02 (write_approved) failed; run "assignment draft hw02" to '
        "start again.",
    )


def test_approve_warns_when_the_intake_changed_after_the_draft_and_still_approves(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys, "draft", "hw02")
    folder = tmp_path / "assignments" / "hw02"
    intake = {**INTAKE, "requirements": {"return_values": INTAKE["requirements"]["return_values"]}}
    write(folder / "intake.yaml", intake)
    requirements_file = folder / "requirements.yaml"
    text = requirements_file.read_text("utf-8")
    requirements_file.write_text(text.replace("severity: major", "severity: minor"), "utf-8")

    code, out = _run(capsys, "approve", "hw02")

    assert code == cli.EXIT_OK
    assert out.startswith(f"Warning: {INTAKE_CHANGED}\nNot applied: Your edits were validated")
    report_path = Path(out.splitlines()[3].strip())
    report = report_path.read_text("utf-8")
    assert report.index(INTAKE_CHANGED) < report.index("Edited by hand")
    code, out = _run(capsys, "approve", "hw02")
    assert code == cli.EXIT_OK
    assert out.splitlines()[:2] == [f"Warning: {INTAKE_CHANGED}", "Approved hw02:"]
    assert "status: draft" not in requirements_file.read_text("utf-8")


def test_approve_ignores_a_change_only_in_a_comment_of_the_intake(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys, "draft", "hw02")
    intake_file = tmp_path / "assignments" / "hw02" / "intake.yaml"
    intake_file.write_text("# A note added after the draft\n" + intake_file.read_text("utf-8"))

    code, out = _run(capsys, "approve", "hw02")

    assert (code, out.splitlines()[0]) == (cli.EXIT_OK, "Approved hw02:")
    assert "Warning" not in out


def test_approve_warns_with_the_reason_when_the_intake_cannot_be_read(
    author: FakeLLMClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(capsys, "draft", "hw02")
    (tmp_path / "assignments" / "hw02" / "intake.yaml").write_text("id: [hw02\n", "utf-8")

    code, out = _run(capsys, "approve", "hw02")

    assert code == cli.EXIT_OK
    warning, approved = out.splitlines()[:2]
    assert warning.startswith(f"Warning: {INTAKE_CHANGED} It cannot be read now: Invalid intake")
    assert "not valid YAML" in warning
    assert approved == "Approved hw02:"


def test_revise_needs_a_note(author: FakeLLMClient) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["assignment", "revise", "hw02"])
