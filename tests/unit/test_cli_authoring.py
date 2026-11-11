"""The `assignment` commands (stage 16): new, then draft until the approval, and approve,
revise or stop it, over the real SQLite checkpointer and a fake author."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from hw_checker import cli
from hw_checker.config.settings import Settings
from hw_checker.graph import AuthoringGraph, build_authoring_graph
from tests.fakes.llm import FakeLLMClient
from tests.unit.services.authoring_data import build_service, make_folders, valid_reply


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


def test_revise_needs_a_note(author: FakeLLMClient) -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["assignment", "revise", "hw02"])
