"""hw02's `no-new-structure` rule (stage 21) is enforced in code, with the real data: an
unclaimed fix that adds a registration or an interface is rejected, in hw02 and hw03."""

from pathlib import Path

import pytest

from hw_checker.domain import Assignment, EditKind, PlannedEdit
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    FILES,
    REPO,
    RESULTS,
    RUN_ID,
    SOURCE,
    FixerSetup,
)

HW02 = next(a for a in SOURCE.load_all() if a.id == "hw02")
HW03 = next(a for a in SOURCE.load_all() if a.id == "hw03")
SELECTED = (ASSIGNMENT, HW02, HW03)
PROGRAM = "Program.cs"
PROGRAM_TEXT = (
    "var builder = WebApplication.CreateBuilder(args);\n"
    "builder.Services.AddControllers();\n"
    "var app = builder.Build();\n"
    "app.Run();\n"
)
CONTROLLERS = "builder.Services.AddControllers();\n"
REGISTRATION_FIX = PlannedEdit(
    file=PROGRAM,
    find=CONTROLLERS,
    replace=(
        f"{CONTROLLERS}//fix: registered the service\n"
        "builder.Services.AddScoped<IUserService, UserService>();\n"
    ),
    kind=EditKind.FIX,
    finding_ids=(),
    reason="The controller needs IUserService.",
    fallback_note=None,
    note_section=None,
)
INTERFACE_FIX = REGISTRATION_FIX.model_copy(
    update={
        "replace": f"{CONTROLLERS}//fix: added the interface\npublic interface IUserService {{ }}\n"
    }
)
REGISTRATION_NOTE = REGISTRATION_FIX.model_copy(
    update={
        "kind": EditKind.NOTE,
        "replace": f"//The assignment asks to register IUserService (AddScoped)\n{CONTROLLERS}",
        "fallback_note": None,
    }
)


@pytest.mark.parametrize("target", [HW02, HW03], ids=["hw02", "hw03"])
def test_an_unclaimed_registration_fix_in_program_is_rejected(
    tmp_path: Path, target: Assignment
) -> None:
    setup = FixerSetup(tmp_path, files={**FILES, PROGRAM: PROGRAM_TEXT})
    workspace = setup.service.prepare(RUN_ID, SELECTED, RESULTS, REPO, setup.fetched)

    application = setup.service.apply(workspace, [REGISTRATION_FIX], target, SELECTED, RESULTS)

    assert application.applied == ()
    [rejected] = application.rejected
    assert rejected.reason == (
        "added code must never register a service (AddScoped, AddTransient, AddSingleton); "
        "ask in a note instead"
    )


def test_an_unclaimed_interface_fix_is_rejected(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files={**FILES, PROGRAM: PROGRAM_TEXT})
    workspace = setup.service.prepare(RUN_ID, SELECTED, RESULTS, REPO, setup.fetched)

    application = setup.service.apply(workspace, [INTERFACE_FIX], HW02, SELECTED, RESULTS)

    [rejected] = application.rejected
    assert rejected.reason == "added code must never declare an interface; ask in a note instead"


def test_a_note_may_still_name_a_registration(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files={**FILES, PROGRAM: PROGRAM_TEXT})
    workspace = setup.service.prepare(RUN_ID, SELECTED, RESULTS, REPO, setup.fetched)

    application = setup.service.apply(workspace, [REGISTRATION_NOTE], HW03, SELECTED, RESULTS)

    assert application.applied == (REGISTRATION_NOTE,)
