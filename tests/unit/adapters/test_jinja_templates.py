from pathlib import Path

import pytest

from hw_checker.adapters.jinja_templates import JinjaTextTemplates
from hw_checker.domain.errors import FixError
from hw_checker.prompts import TEMPLATES_DIR


def test_a_template_renders_its_values(tmp_path: Path) -> None:
    (tmp_path / "hello.md.j2").write_text("Hello {{ name }}!\n", encoding="utf-8")

    assert JinjaTextTemplates(tmp_path).render("hello.md.j2", {"name": "Dana"}) == "Hello Dana!\n"


def test_a_missing_value_or_template_is_a_fix_error(tmp_path: Path) -> None:
    (tmp_path / "hello.md.j2").write_text("Hello {{ name }}!", encoding="utf-8")
    templates = JinjaTextTemplates(tmp_path)

    with pytest.raises(FixError, match=r"hello.md.j2"):
        templates.render("hello.md.j2", {})
    with pytest.raises(FixError, match=r"missing.md.j2"):
        templates.render("missing.md.j2", {})


def test_the_version_changes_when_a_template_changes(tmp_path: Path) -> None:
    template = tmp_path / "hello.md.j2"
    template.write_text("Hello", encoding="utf-8")
    templates = JinjaTextTemplates(tmp_path)
    before = templates.version()

    template.write_text("Hello again", encoding="utf-8")

    assert templates.version() != before


@pytest.mark.parametrize(
    "name", ["fixer_plan.md.j2", "fixer_review.md.j2", "pr_text.md.j2", "pr_body.md.j2"]
)
def test_every_prompt_template_ships_with_the_package(name: str) -> None:
    assert (TEMPLATES_DIR / name).is_file()
