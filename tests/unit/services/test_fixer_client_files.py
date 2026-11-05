"""Stage 18: the fixer reviews the files an assignment lists besides the code (ADR 0023)."""

import re
from pathlib import Path

import pytest

from hw_checker.domain import EditKind, PlannedEdit, ReviewedFiles
from tests.unit.services.fixer_data import (
    ASSIGNMENT,
    FILES,
    HW01_REVIEWED,
    REPO,
    RESULTS,
    RUN_ID,
    FixerSetup,
)

UPDATE_JS = 'const input = document.getElementById("firstname");\n'
CLIENT_FILES = FILES | {
    "Api/wwwroot/update.html": '<input id="name">\n<script src="update.js"></script>\n',
    "Api/wwwroot/update.js": UPDATE_JS,
    "Api/wwwroot/css/site.css": "form { display: block; }\n",
    "Api/wwwroot/js/site.min.js": "var a=1;\n",
    "Api/wwwroot/lib/jquery/jquery.js": "/* a library */\n",
    "tools/build.js": "// not in wwwroot\n",
}
"""The synthetic repo with a client folder, a minified script, a library and a script
outside the client folder."""
CLIENT_ASSIGNMENT = ASSIGNMENT.model_copy(update={"reviewed_files": HW01_REVIEWED})


def _shown(prompt: str) -> list[str]:
    return re.findall(r'<student_file path="([^"]+)">', prompt)


@pytest.mark.parametrize(
    ("path", "reviewed"),
    [
        ("wwwroot/index.html", True),
        ("Api/wwwroot/js/update.js", True),
        ("Api/WWWRoot/Site.CSS", True),
        ("Api/wwwroot/js/site.min.js", False),
        ("Api/wwwroot/css/bootstrap.min.css", False),
        ("Api/wwwroot/lib/jquery/jquery.js", False),
        ("tools/build.js", False),
        ("Api/wwwroot/data.json", False),
    ],
)
def test_hw01_reviews_the_client_files_but_never_a_library(path: str, reviewed: bool) -> None:
    assert HW01_REVIEWED is not None
    assert HW01_REVIEWED.matches(path) is reviewed


def test_reviewed_files_need_at_least_one_glob() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ReviewedFiles(include=())


def test_the_prompt_shows_the_code_then_the_client_files_then_configuration_and_data(
    tmp_path: Path,
) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(
        workspace, CLIENT_ASSIGNMENT, (CLIENT_ASSIGNMENT,), RESULTS, 1
    )

    assert _shown(prompt) == [
        "Controllers/UsersController.cs",
        "Models/User.cs",
        "Api.csproj",
        "Api/wwwroot/css/site.css",
        "Api/wwwroot/update.html",
        "Api/wwwroot/update.js",
        "appsettings.json",
        "users.json",
    ]


def test_without_reviewed_files_only_the_code_is_shown_as_before(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)

    prompt = setup.service.plan_prompt(workspace, ASSIGNMENT, (ASSIGNMENT,), RESULTS, 1)

    assert not [path for path in _shown(prompt) if "wwwroot" in path]
    assert "Only `.cs` files are edited" in prompt


def _note(file: str, find: str, comment: str) -> PlannedEdit:
    return PlannedEdit(
        file=file,
        find=find,
        replace=f"{comment}\n{find}",
        kind=EditKind.NOTE,
        finding_ids=(),
        reason="A question for the student.",
        fallback_note=None,
        note_section=None,
    )


def test_only_the_files_shown_with_a_comment_style_are_edited(tmp_path: Path) -> None:
    setup = FixerSetup(tmp_path, files=CLIENT_FILES)
    workspace = setup.service.prepare(RUN_ID, "hw01", REPO, setup.fetched)
    shown_script = _note("Api/wwwroot/update.js", UPDATE_JS, "//Is this the input's id?")
    library = _note("Api/wwwroot/js/site.min.js", "var a=1;\n", "//A question")
    outside = _note("tools/build.js", "// not in wwwroot\n", "//A question")
    project = _note("Api.csproj", "<Project", "<!--A question-->")

    application = setup.service.apply(
        workspace,
        [shown_script, library, outside, project],
        CLIENT_ASSIGNMENT,
        (CLIENT_ASSIGNMENT,),
    )

    assert application.applied == (shown_script,)
    assert [rejected.reason for rejected in application.rejected] == [
        "Api/wwwroot/js/site.min.js was not shown to you; only the files shown are edited",
        "tools/build.js was not shown to you; only the files shown are edited",
        "Api.csproj is not a .cs or .js or .html or .css file; only .cs or .js or .html or "
        ".css files are edited",
    ]
