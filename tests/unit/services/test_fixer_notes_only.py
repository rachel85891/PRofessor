"""Requirements with `feedback: notes_only` get notes, never a fix (stage 20)."""

from pathlib import Path

from hw_checker.domain import (
    Assignment,
    AssignmentResult,
    EditKind,
    Feedback,
    NoteSection,
    RequirementKind,
)
from hw_checker.services.fixing import FixService
from tests.unit.domain import builders
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    BREAK_FIX,
    CONTROLLER,
    MODEL,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

LAYERS = Assignment(
    id="hw02",
    order=2,
    title="Layers",
    requirements=(
        builders.requirement(
            id="hw02-layers",
            title="The data access is in its own layer",
            kind=RequirementKind.BUILD,
            feedback=Feedback.NOTES_ONLY,
        ),
    ),
)
SELECTED = (ASSIGNMENT, LAYERS)
LAYERS_FINDING = builders.finding(
    id="hw02-layers-abc",
    requirement_id="hw02-layers",
    assignment_id="hw02",
    message_en="The model is read and written in the controller.",
    evidence=builders.evidence(file_path=MODEL, line_start=1, line_end=1, quote="public class"),
)
WITH_LAYERS = (*RESULTS, AssignmentResult(assignment_id="hw02", findings=(LAYERS_FINDING,)))
MODEL_FIX = BREAK_FIX.model_copy(
    update={
        "file": MODEL,
        "find": "    public int id { get; set; }\n",
        "replace": "    //fix: Id is PascalCase\n    public int Id { get; set; }\n",
        "finding_ids": (),
        "reason": "Properties are PascalCase in C#.",
    }
)
MODEL_NOTE = MODEL_FIX.model_copy(
    update={
        "kind": EditKind.NOTE,
        "replace": "    //Could reading users move to a repository class?\n"
        "    public int id { get; set; }\n",
        "finding_ids": ("hw02-layers",),
        "fallback_note": None,
        "note_section": NoteSection.FOR_YOU,
    }
)


def test_the_guard_rejects_fixes_and_the_rejection_reaches_the_next_plan(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, SELECTED, WITH_LAYERS, REPO, setup.fetched)
    claimed = BREAK_FIX.model_copy(update={"finding_ids": ("hw02-layers",)})

    application = setup.service.apply(
        workspace, [claimed, MODEL_FIX, MODEL_NOTE], LAYERS, SELECTED, WITH_LAYERS
    )

    assert application.applied == (MODEL_NOTE,)
    assert FixService.feedback(application, None) == [
        f"Rejected fix in {CONTROLLER}: hw02-layers allows notes only; plan a note, not a fix",
        f"Rejected fix in {MODEL}: only notes-only findings point to {MODEL}, so it gets notes "
        "only; plan a note, not a fix",
    ]


def test_without_a_notes_only_finding_the_guard_stays_out_of_the_way(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, SELECTED, RESULTS, REPO, setup.fetched)

    application = setup.service.apply(workspace, [BREAK_FIX, MODEL_FIX], LAYERS, SELECTED, RESULTS)

    assert application.applied == (BREAK_FIX,)
    # The model fix is rejected by the course's own rule (a stored property keeps its
    # name), never by the notes-only guard.
    assert not any("notes only" in rejected.reason for rejected in application.rejected)


def test_the_plan_prompt_names_the_failing_notes_only_requirements(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path)
    workspace = setup.service.prepare(RUN_ID, SELECTED, WITH_LAYERS, REPO, setup.fetched)

    failing = setup.service.plan_prompt(workspace, LAYERS, SELECTED, WITH_LAYERS, 1)
    passing = setup.service.plan_prompt(workspace, LAYERS, SELECTED, RESULTS, 1)

    assert ". Requirements `hw02-layers` allow notes only: for their findings, plan `note`" in (
        failing
    )
    assert "allow notes only" not in passing
