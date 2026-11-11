"""The author's input after stage 21, on the committed course and assignments: the new
checks and the earlier assignments' files reach the author with no change to the
authoring code. The new assignment's intake and task text are synthetic."""

import shutil
from pathlib import Path

from hw_checker.config.settings import REPO_ROOT
from hw_checker.services.authoring import AuthoringService
from tests.unit.services.authoring_data import build_service, write

STAGE_21_CHECKS = (
    "code_only_in_layer",
    "constructor_takes_interface",
    "implements_interface",
    "interfaces_registered",
    "layer_projects",
    "layer_references",
    "no_concrete_construction",
)


def _hw04(root: Path) -> AuthoringService:
    shutil.copytree(REPO_ROOT / "course", root / "course")
    shutil.copytree(REPO_ROOT / "assignments", root / "assignments")
    folder = root / "assignments" / "hw04"
    write(folder / "intake.yaml", {"id": "hw04", "order": 4, "title": "A synthetic task"})
    write(folder / "task.md", "Add a synthetic endpoint.\n")
    return build_service(root)


def test_the_catalog_offers_stage_21s_checks(tmp_path: Path) -> None:
    service = _hw04(tmp_path)

    prompt = service.author_prompt(service.load_intake("hw04"))

    for name in STAGE_21_CHECKS:
        assert f"- `{name}`" in prompt


def test_the_author_sees_every_earlier_assignments_files(tmp_path: Path) -> None:
    service = _hw04(tmp_path)

    prompt = service.author_prompt(service.load_intake("hw04"))

    for assignment_id in ("_general", "hw01", "hw02", "hw03"):
        assert f'<data_file path="assignments/{assignment_id}/requirements.yaml">' in prompt
    for assignment_id in ("hw01", "hw02", "hw03"):
        assert f'<data_file path="assignments/{assignment_id}/review_rules.yaml">' in prompt
    assert '<data_file path="assignments/hw04/' not in prompt
