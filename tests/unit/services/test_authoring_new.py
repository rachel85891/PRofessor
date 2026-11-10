"""`assignment new`: the intake template, carrying `taught` over, and refusing an id that
exists (stage 16, design §8.3)."""

from pathlib import Path

import pytest
import yaml

from hw_checker.adapters.assignments_yaml.authoring import YamlAuthoringFiles
from hw_checker.adapters.jinja_templates import JinjaTextTemplates
from hw_checker.domain import Assignment
from hw_checker.domain.authoring import AssignmentIntake
from hw_checker.domain.errors import AuthoringError, InvalidAssignmentError
from hw_checker.prompts import TEMPLATES_DIR
from hw_checker.services.authoring import AuthoringService
from tests.fakes.assignments import FakeAssignmentSource
from tests.fakes.checks import FakeCheckCatalog
from tests.fakes.llm import FakeLLMClient

GENERAL = Assignment(id="_general", order=0, title="General", requirements=())
HW01 = Assignment(id="hw01", order=1, title="Users", requirements=())


def _service(assignments_dir: Path) -> AuthoringService:
    return AuthoringService(
        YamlAuthoringFiles(assignments_dir, assignments_dir.parent / "course"),
        FakeAssignmentSource([GENERAL, HW01]),
        JinjaTextTemplates(TEMPLATES_DIR),
        FakeCheckCatalog(),
        FakeLLMClient(),
    )


def _intake(folder: Path) -> AssignmentIntake:
    return AssignmentIntake.model_validate(
        yaml.safe_load((folder / "intake.yaml").read_text(encoding="utf-8"))
    )


def test_new_writes_a_commented_intake_that_loads_and_an_empty_task(tmp_path: Path) -> None:
    folder = _service(tmp_path).new_assignment("hw02")

    text = (folder / "intake.yaml").read_text(encoding="utf-8")
    intake = _intake(folder)
    assert folder == tmp_path / "hw02"
    assert (folder / "task.md").read_bytes() == b""
    assert text.startswith("# yaml-language-server: $schema=../../course/schemas/intake.schema")
    assert "# 4. The task's requirements, by heading." in text
    assert (intake.id, intake.order, intake.check_commit) == ("hw02", 2, "latest")
    assert intake.items() == ()


def test_new_carries_taught_over_from_the_previous_intake(tmp_path: Path) -> None:
    previous = tmp_path / "hw02"
    previous.mkdir()
    (previous / "intake.yaml").write_text(
        "id: hw02\norder: 2\ntitle: Layers\ntaught: [controllers, 'layers: API, Services']\n"
        "not_yet_taught: [dependency injection]\n",
        encoding="utf-8",
    )

    intake = _intake(_service(tmp_path).new_assignment("hw03"))

    assert intake.order == 3
    assert intake.taught == ("controllers", "layers: API, Services")
    assert intake.not_yet_taught == ("dependency injection",)


def test_new_takes_the_previous_intake_by_order_not_a_later_one(tmp_path: Path) -> None:
    for assignment_id, order, taught in (("hw02", 2, "two"), ("hw05", 5, "five")):
        folder = tmp_path / assignment_id
        folder.mkdir()
        (folder / "intake.yaml").write_text(
            f"id: {assignment_id}\norder: {order}\ntitle: T\ntaught: [{taught}]\n",
            encoding="utf-8",
        )

    intake = _intake(_service(tmp_path).new_assignment("hw03", order=3))

    assert intake.taught == ("two",)


def test_new_refuses_an_id_that_exists_and_changes_nothing(tmp_path: Path) -> None:
    (tmp_path / "hw02").mkdir()
    (tmp_path / "hw02" / "task.md").write_text("mine", encoding="utf-8")

    with pytest.raises(AuthoringError, match="already exists"):
        _service(tmp_path).new_assignment("hw02")
    assert (tmp_path / "hw02" / "task.md").read_text(encoding="utf-8") == "mine"
    assert not (tmp_path / "hw02" / "intake.yaml").exists()


@pytest.mark.parametrize("bad", ["_general", "../hw02", "hw 02", ""])
def test_new_refuses_an_id_that_is_not_a_folder_name(tmp_path: Path, bad: str) -> None:
    with pytest.raises(AuthoringError, match="cannot be an assignment id"):
        _service(tmp_path).new_assignment(bad)


def test_an_invalid_intake_lists_every_problem(tmp_path: Path) -> None:
    folder = tmp_path / "hw02"
    folder.mkdir()
    (folder / "intake.yaml").write_text(
        "id: hw02\norder: 0\ntitle: T\nrequirementz: []\n", encoding="utf-8"
    )

    with pytest.raises(InvalidAssignmentError) as raised:
        YamlAuthoringFiles(tmp_path, tmp_path / "course").load_intake("hw02")

    message = str(raised.value)
    assert "field 'order'" in message
    assert "field 'requirementz': unknown key" in message


def test_a_missing_intake_says_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(AuthoringError, match="assignment new hw02"):
        YamlAuthoringFiles(tmp_path, tmp_path / "course").load_intake("hw02")
