"""`until` in a draft: the author's reply accepts it on a requirement and on a lenient
note, the loader's validation applies, and the report lists what ended."""

from pathlib import Path

import yaml

from hw_checker.adapters.assignments_yaml.authoring import YamlAuthoringFiles
from hw_checker.domain import LenientNote
from hw_checker.domain.authoring import AuthorReply, ValidationReport
from hw_checker.services.authoring import AuthoringService
from tests.unit.services.authoring_data import (
    build_service,
    make_folders,
    not_yet_taught_rule,
    post_requirement,
    projects_requirement,
    valid_reply,
    write,
)


def _validate(service: AuthoringService, reply: AuthorReply) -> ValidationReport:
    return service.validate(service.load_intake("hw02"), reply)


def _reply(*, until: str | None = None, note_until: str | None = None) -> AuthorReply:
    return AuthorReply(
        requirements=(post_requirement(until=until), projects_requirement()),
        rules=(not_yet_taught_rule(),),
        lenient_notes=("Plain note", LenientNote(text="Ending note", until=note_until)),
    )


def test_a_valid_until_on_a_requirement_and_a_note_passes(tmp_path: Path) -> None:
    report = _validate(build_service(tmp_path), _reply(until="hw02", note_until="hw02"))

    assert report.passed, report.reasons


def test_an_invalid_until_is_a_problem_naming_the_item(tmp_path: Path) -> None:
    report = _validate(build_service(tmp_path), _reply(until="hw09", note_until="hw01"))

    reasons = "\n".join(report.reasons)
    assert not report.passed
    assert "requirement 'hw02-post', field 'until': unknown assignment 'hw09'" in reasons
    assert "lenient note 2 (\"Ending note\"), field 'until': 'hw01' comes before hw02" in reasons


def test_the_draft_files_write_each_note_in_its_form(tmp_path: Path) -> None:
    service = build_service(tmp_path)

    folders = YamlAuthoringFiles(tmp_path / "assignments", tmp_path / "course", None, None)

    files = folders.draft_files(service.load_intake("hw02"), _reply(note_until="hw03"), None)

    written = yaml.safe_load(files.requirements)
    assert written["lenient_notes"] == ["Plain note", {"text": "Ending note", "until": "hw03"}]


def test_the_report_lists_the_requirements_and_notes_that_ended(tmp_path: Path) -> None:
    make_folders(tmp_path)
    write(
        tmp_path / "assignments" / "hw01" / "requirements.yaml",
        """\
id: hw01
order: 1
title: Users
lenient_notes:
  - {text: Text files are fine, until: hw01}
  - Keep going
requirements:
  - {id: hw01-data-file, kind: build, title: Data file, severity: minor, until: hw01}
  - {id: hw01-builds, kind: build, title: Builds, severity: minor}
""",
    )
    service = build_service(tmp_path)
    reply = valid_reply()
    report = _validate(service, reply)

    assert report.changes.ended_requirements == ("hw01-data-file",)
    assert report.changes.ended_notes == ("Text files are fine",)
    text = service.write_report(service.load_intake("hw02"), reply, report, 1, 3).read_text(
        encoding="utf-8"
    )
    assert "- Requirements ended: `hw01-data-file`" in text
    assert "- Lenient notes ended: Text files are fine" in text


def test_nothing_ended_means_no_change(tmp_path: Path) -> None:
    changes = _validate(build_service(tmp_path), valid_reply()).changes

    assert changes.ended_requirements == () and changes.ended_notes == ()
