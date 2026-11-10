"""The authoring graph (stage 16, design §7 and §8.3): the draft and validation loop, the
approval interrupt, and what approving, revising and stopping write. A fake LLM returns
canned drafts; the data is synthetic."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.bootstrap import checkpoint_serializer
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import ItemStatus
from hw_checker.domain.authoring import AuthoringOutcome, AuthoringPrompt, AuthorReply
from hw_checker.domain.errors import InvalidAssignmentError, LLMOutputError
from hw_checker.graph import AuthoringGraph, AuthoringInput, build_authoring_graph
from hw_checker.services.assignments import AssignmentCatalogService
from tests.fakes.llm import FakeLLMClient
from tests.unit.services.authoring_data import (
    build_service,
    post_requirement,
    projects_requirement,
    valid_reply,
)

CONFIG: RunnableConfig = {"configurable": {"thread_id": "authoring-hw02"}}
START = AuthoringInput(assignment_id="hw02")
INVALID = AuthorReply(
    requirements=(
        post_requirement(),
        projects_requirement(check="project_counts"),
        projects_requirement(id="hw02-projects-2", params={"minimum": 4}),
    )
)
"""An unknown check, bad params, and an uncovered intake item (`not_yet_taught.1`)."""


def _graph(root: Path, llm: FakeLLMClient, max_attempts: int = 3) -> AuthoringGraph:
    checkpointer = InMemorySaver(serde=checkpoint_serializer())
    return build_authoring_graph(build_service(root, llm), max_attempts, 2, checkpointer)


def _prompt(output: dict[str, Any]) -> AuthoringPrompt:
    return AuthoringPrompt.model_validate(output["__interrupt__"][0].value)


def _round_catalog(root: Path) -> AssignmentCatalogService:
    source = YamlAssignmentSource(root / "assignments", root / "course")
    return AssignmentCatalogService(source, CheckRegistry(builtin_checks()))


def _files(root: Path) -> tuple[Path, Path]:
    folder = root / "assignments" / "hw02"
    return folder / "requirements.yaml", folder / "review_rules.yaml"


def test_an_invalid_draft_goes_back_with_the_reasons_then_a_valid_one_waits(
    tmp_path: Path,
) -> None:
    llm = FakeLLMClient({"author": [INVALID, valid_reply()]})

    output = _graph(tmp_path, llm).invoke(START, CONFIG)

    prompt = _prompt(output)
    assert prompt.attempt == 2
    assert [call.role for call in llm.calls] == ["author", "author"]
    second = llm.calls[1].prompt
    assert "unknown check 'project_counts'" in second
    assert "param 'minimum': unknown param" in second
    assert "intake item `not_yet_taught.1` is not covered" in second
    assert "<previous_draft>" in second
    assert Path(prompt.report_path).is_file()


def test_max_attempts_reached_stops_with_the_report_and_writes_nothing(tmp_path: Path) -> None:
    llm = FakeLLMClient({"author": [INVALID]})

    output = _graph(tmp_path, llm, max_attempts=2).invoke(START, CONFIG)

    assert "__interrupt__" not in output
    assert output["outcome"] is AuthoringOutcome.FAILED
    assert len(llm.calls) == 2
    report = Path(output["report_path"]).read_text(encoding="utf-8")
    assert "- Validation: FAILED" in report
    assert "nothing was written to the assignment's folder" in report
    assert not any(path.exists() for path in _files(tmp_path))


def test_while_waiting_the_files_are_drafts_and_a_round_refuses_them(tmp_path: Path) -> None:
    _graph(tmp_path, FakeLLMClient({"author": [valid_reply()]})).invoke(START, CONFIG)

    requirements = yaml.safe_load(_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert {item["status"] for item in requirements["requirements"]} == {"draft"}
    with pytest.raises(InvalidAssignmentError, match="these items are drafts") as refused:
        _round_catalog(tmp_path).select_for_round("hw02")
    assert "hw02: requirement 'hw02-post'" in str(refused.value)
    assert "hw02: rule 'hw02-not-yet-taught'" in str(refused.value)


def test_approve_writes_approved_files_and_a_round_accepts_them(tmp_path: Path) -> None:
    graph = _graph(tmp_path, FakeLLMClient({"author": [valid_reply()]}))
    graph.invoke(START, CONFIG)

    output = graph.invoke(Command(resume={"action": "approve"}), CONFIG)

    assert output["outcome"] is AuthoringOutcome.APPROVED
    requirements_file, rules_file = _files(tmp_path)
    assert output["written"] == [str(requirements_file), str(rules_file)]
    text = requirements_file.read_text(encoding="utf-8")
    assert text.splitlines()[0] == (
        "# Approved on 2026-10-29 through the authoring graph (design §8.3); origins kept."
    )
    data = yaml.safe_load(text)
    assert data["taught_techniques"] == ["controllers", "layers"]
    assert data["requirements"][1]["origin"] == {
        "source": "intake",
        "quote": "At least four projects",
        "item": "requirements.architecture.1",
    }
    assert "summary" not in data["requirements"][0]
    selected = _round_catalog(tmp_path).select_for_round("hw02")
    hw02 = next(a for a in selected if a.id == "hw02")
    assert all(r.status is ItemStatus.APPROVED for r in hw02.requirements)
    assert hw02.review_rules.rules[0].status is ItemStatus.APPROVED
    probe = hw02.requirements[0].http_probes[0]
    assert (probe.body, probe.allowed_statuses) == ('{"userName": "newuser"}', (201, 200))


def test_revise_drafts_again_with_the_notes_without_counting_an_attempt(tmp_path: Path) -> None:
    llm = FakeLLMClient({"author": [valid_reply()]})
    graph = _graph(tmp_path, llm, max_attempts=1)
    graph.invoke(START, CONFIG)

    output = graph.invoke(
        Command(resume={"action": "revise", "notes": ["Use 201 only for POST"]}), CONFIG
    )

    assert _prompt(output).attempt == 1
    assert len(llm.calls) == 2
    assert "- Use 201 only for POST" in llm.calls[1].prompt


def test_stop_writes_nothing_and_removes_the_drafted_files(tmp_path: Path) -> None:
    graph = _graph(tmp_path, FakeLLMClient({"author": [valid_reply()]}))
    graph.invoke(START, CONFIG)

    output = graph.invoke(Command(resume={"action": "stop"}), CONFIG)

    assert output["outcome"] is AuthoringOutcome.STOPPED
    assert not any(path.exists() for path in _files(tmp_path))
    assert (tmp_path / "assignments" / "hw02" / "intake.yaml").is_file()


def test_files_edited_while_waiting_are_not_approved_or_overwritten(tmp_path: Path) -> None:
    graph = _graph(tmp_path, FakeLLMClient({"author": [valid_reply()]}))
    graph.invoke(START, CONFIG)
    requirements_file = _files(tmp_path)[0]
    edited = requirements_file.read_text(encoding="utf-8") + "# my edit\n"
    requirements_file.write_text(edited, encoding="utf-8")

    output = graph.invoke(Command(resume={"action": "approve"}), CONFIG)

    assert "were changed since the report was written" in _prompt(output).message
    assert requirements_file.read_text(encoding="utf-8") == edited


def test_a_wrong_answer_is_refused_and_the_interrupt_stays_open(tmp_path: Path) -> None:
    graph = _graph(tmp_path, FakeLLMClient({"author": [valid_reply()]}))
    graph.invoke(START, CONFIG)

    with pytest.raises(Exception, match="revise needs at least one note"):
        graph.invoke(Command(resume={"action": "revise"}), CONFIG)
    assert graph.get_state(CONFIG).interrupts


def test_an_unreadable_reply_is_asked_for_again(tmp_path: Path) -> None:
    llm = FakeLLMClient({"author": [LLMOutputError("unreadable"), valid_reply()]})

    output = _graph(tmp_path, llm).invoke(START, CONFIG)

    assert _prompt(output).attempt == 1
    assert len(llm.calls) == 2


def test_the_thread_starts_afresh_after_it_ended(tmp_path: Path) -> None:
    llm = FakeLLMClient({"author": [valid_reply()]})
    graph = _graph(tmp_path, llm)
    graph.invoke(START, CONFIG)
    graph.invoke(Command(resume={"action": "stop"}), CONFIG)

    output = graph.invoke(START, CONFIG)

    assert _prompt(output).attempt == 1
    assert "<previous_draft>" not in llm.calls[-1].prompt
