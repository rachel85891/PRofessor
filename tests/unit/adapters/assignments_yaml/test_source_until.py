"""`until` on a requirement and on a lenient note: how it loads and what the loader refuses."""

from collections.abc import Mapping
from pathlib import Path

import pytest

from hw_checker.adapters.assignments_yaml import YamlAssignmentSource
from hw_checker.domain.errors import InvalidAssignmentError
from tests.unit.adapters.assignments_yaml.test_source import load, problems_of

GENERAL = """\
id: _general
order: 0
title: General
lenient_notes:
  - text: Storing data in a text file is expected
    until: hw02
  - A note that never ends
requirements:
  - id: gen-r1
    kind: build
    title: Builds
    severity: minor
    until: hw02
"""


def load_tree(root: Path, files: Mapping[str, str]) -> YamlAssignmentSource:
    return load(root, dict[str, str | bytes](files))


def assignment(name: str, order: int, requirements: str = "", notes: str = "") -> str:
    return f"id: {name}\norder: {order}\ntitle: {name}\n{notes}requirements:\n" + (
        requirements or "  - {id: " + name + "-r1, kind: build, title: B, severity: minor}\n"
    )


def requirement(item: str, until: str | None = None, depends_on: str | None = None) -> str:
    extra = (f", until: {until}" if until else "") + (
        f", depends_on: [{depends_on}]" if depends_on else ""
    )
    return f"  - {{id: {item}, kind: build, title: B, severity: minor{extra}}}\n"


def test_until_loads_on_requirements_and_in_both_note_forms(tmp_path: Path) -> None:
    source = load(
        tmp_path,
        {
            "_general": GENERAL,
            "hw01": assignment("hw01", 1),
            "hw02": assignment("hw02", 2),
            "hw03": assignment("hw03", 3),
        },
    )

    general = source.load_all()[0]

    assert general.requirements[0].until == "hw02"
    assert general.lenient_notes == (
        "Storing data in a text file is expected",
        "A note that never ends",
    )
    assert [(n.text, n.until) for n in general.lenient_note_ends] == [
        ("Storing data in a text file is expected", "hw02")
    ]


def test_an_item_may_end_at_its_own_assignment(tmp_path: Path) -> None:
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1, requirement("hw01-r1", until="hw01")),
    }

    assert load_tree(tmp_path, files).load_all()[1].requirements[0].until == "hw01"


@pytest.mark.parametrize("until", ["hw09", ""])
def test_unknown_until_names_the_requirement(tmp_path: Path, until: str) -> None:
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1, requirement("hw01-r1", until=until or "nope")),
    }

    message = problems_of(load_tree(tmp_path, files))

    assert "requirement 'hw01-r1', field 'until': unknown assignment" in message


def test_unknown_until_on_a_note_names_the_note(tmp_path: Path) -> None:
    notes = "lenient_notes:\n  - {text: Be kind to beginners, until: hw07}\n"
    files = {"_general": assignment("_general", 0, notes=notes)}

    message = problems_of(load_tree(tmp_path, files))

    assert (
        "lenient note 1 (\"Be kind to beginners\"), field 'until': unknown assignment 'hw07'"
        in message
    )


def test_until_earlier_than_the_items_own_assignment_is_refused(tmp_path: Path) -> None:
    notes = "lenient_notes:\n  - {text: A note, until: hw01}\n"
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1),
        "hw02": assignment("hw02", 2, requirement("hw02-r1", until="hw01"), notes),
    }

    message = problems_of(load_tree(tmp_path, files))

    assert "requirement 'hw02-r1', field 'until': 'hw01' comes before hw02" in message
    assert "lenient note 1 (\"A note\"), field 'until': 'hw01' comes before hw02" in message


@pytest.mark.parametrize("until", [None, "hw03"])
def test_a_dependent_that_outlives_its_dependency_is_refused(
    tmp_path: Path, until: str | None
) -> None:
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1, requirement("hw01-r1", until="hw02")),
        "hw02": assignment("hw02", 2, requirement("hw02-r2", until, "hw01-r1")),
        "hw03": assignment("hw03", 3),
    }

    message = problems_of(load_tree(tmp_path, files))

    assert "requirement 'hw02-r2', field 'depends_on': 'hw01-r1' ends at hw02" in message


def test_a_dependency_that_ends_with_or_after_its_dependent_is_accepted(tmp_path: Path) -> None:
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1, requirement("hw01-r1", until="hw03")),
        "hw02": assignment("hw02", 2, requirement("hw02-r2", until="hw02", depends_on="hw01-r1")),
        "hw03": assignment("hw03", 3, requirement("hw03-r3", until="hw03", depends_on="hw01-r1")),
    }

    assert len(load_tree(tmp_path, files).load_all()) == 4


def test_a_dependency_without_an_end_is_always_accepted(tmp_path: Path) -> None:
    files = {
        "_general": assignment("_general", 0),
        "hw01": assignment("hw01", 1),
        "hw02": assignment("hw02", 2, requirement("hw02-r2", depends_on="hw01-r1")),
    }

    assert len(load_tree(tmp_path, files).load_all()) == 3


def test_a_note_object_needs_text(tmp_path: Path) -> None:
    files = {"_general": assignment("_general", 0, notes="lenient_notes:\n  - {until: hw01}\n")}

    with pytest.raises(InvalidAssignmentError):
        load_tree(tmp_path, files).load_all()
