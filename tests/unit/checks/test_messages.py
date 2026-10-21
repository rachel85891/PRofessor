import re
import string
from pathlib import Path

import pytest

from hw_checker.checks.deterministic import messages
from hw_checker.checks.deterministic.messages import ALL_TEMPLATES, Localized, MessageTemplate

DETERMINISTIC_PACKAGE = Path(messages.__file__).parent
HEBREW = re.compile(r"[֐-׿]")


def fields_of(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("template", ALL_TEMPLATES, ids=lambda t: t.name)
def test_template_renders_in_both_languages(template: MessageTemplate) -> None:
    fields = fields_of(template.en)
    values = {name: f"<{name}>" for name in fields}

    rendered = template.render(**values)

    assert fields_of(template.he) == fields, "Hebrew and English must use the same fields"
    assert all(f"<{name}>" in rendered.en and f"<{name}>" in rendered.he for name in fields)
    assert HEBREW.search(template.he), "the reviewer message is in Hebrew"
    assert not HEBREW.search(template.en), "the student message is in English"


def test_localized_fields_use_the_matching_language() -> None:
    template = MessageTemplate(name="T", he="[{part}]", en="[{part}]")

    assert template.render(part=Localized(he="שלום", en="hello")) == Localized("[שלום]", "[hello]")


def test_every_template_is_listed_and_named_after_its_constant() -> None:
    constants = {
        name: value for name, value in vars(messages).items() if isinstance(value, MessageTemplate)
    }

    assert set(constants.values()) == set(ALL_TEMPLATES)
    assert all(name == template.name for name, template in constants.items())


def test_no_template_is_unused() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in DETERMINISTIC_PACKAGE.glob("*.py")
        if path.name != "messages.py"
    )

    unused = [t.name for t in ALL_TEMPLATES if not re.search(rf"\b{t.name}\b", sources)]
    assert unused == []
