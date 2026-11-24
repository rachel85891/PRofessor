"""The author's catalog offers the class-placement checks (HW05-G3) with their params
schema, with no change to the authoring code. The intake and task text are synthetic."""

import shutil
from pathlib import Path

from hw_checker.config.settings import REPO_ROOT
from hw_checker.services.authoring import AuthoringService
from tests.unit.services.authoring_data import build_service, write


def _service(root: Path) -> AuthoringService:
    shutil.copytree(REPO_ROOT / "course", root / "course")
    shutil.copytree(REPO_ROOT / "assignments", root / "assignments")
    folder = root / "assignments" / "hw04"
    write(folder / "intake.yaml", {"id": "hw04", "order": 4, "title": "A synthetic task"})
    write(folder / "task.md", "Add a synthetic endpoint.\n")
    return build_service(root)


def test_the_catalog_offers_the_class_checks_with_the_selector_schema(tmp_path: Path) -> None:
    service = _service(tmp_path)

    prompt = service.author_prompt(service.load_intake("hw04"))

    assert "- `classes_in_role`" in prompt
    assert "- `namespace_matches_project`" in prompt
    assert '"entities_of_bases"' in prompt
    assert '"ClassSelector"' in prompt
