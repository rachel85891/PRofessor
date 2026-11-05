"""`project_count`, and structure checks on a commit's file list instead of the working copy."""

from pathlib import Path

import pytest

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.deterministic.structure import (
    FileExistsParams,
    FolderExistsParams,
    ProjectCountParams,
    file_exists,
    folder_exists,
    project_count,
    tree_from_files,
)
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import Assignment, CheckInput, Requirement, RequirementKind, Severity
from hw_checker.domain.errors import InvalidAssignmentError

REQUIREMENT = Requirement(
    id="hw02-layers",
    title="The solution has layers",
    severity=Severity.MAJOR,
    kind=RequirementKind.STRUCTURE,
    check="project_count",
    params={"min": 2},
)
ASSIGNMENT = Assignment(id="hw02", order=2, title="Layers", requirements=(REQUIREMENT,))


def on_files(*files: str) -> CheckInput:
    return CheckInput(
        repo_dir=Path("unused"), assignment=ASSIGNMENT, requirement=REQUIREMENT, files=files
    )


def write(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<Project />", encoding="utf-8")


def test_two_projects_pass_and_one_is_a_finding_with_the_count() -> None:
    params = ProjectCountParams(min=2)

    assert project_count(on_files("Api/Api.csproj", "Entities/Entities.CSPROJ"), params) == ()
    [finding] = project_count(on_files("Api/Api.csproj"), params)
    assert finding.message_en == "At least 2 projects (.csproj files) were expected; 1 found."


def test_projects_under_bin_obj_and_git_do_not_count() -> None:
    files = ("Api/Api.csproj", "Api/bin/Debug/Api.csproj", "obj/X.csproj", ".git/y.csproj")

    assert len(project_count(on_files(*files), ProjectCountParams(min=2))) == 1


def test_project_count_reads_the_working_copy_when_no_file_list_is_given(tmp_path: Path) -> None:
    write(tmp_path, "Api/Api.csproj")
    write(tmp_path, "Entities/Entities.csproj")
    check_input = CheckInput(repo_dir=tmp_path, assignment=ASSIGNMENT, requirement=REQUIREMENT)

    assert project_count(check_input, ProjectCountParams(min=2)) == ()


def test_min_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        ProjectCountParams(min=0)


def test_tree_from_files_derives_folders_and_drops_build_output() -> None:
    tree = tree_from_files(("Api/Controllers/UsersController.cs", "Api/obj/x.json", "README.md"))

    assert tree.files == ("Api/Controllers/UsersController.cs", "README.md")
    assert tree.folders == ("Api", "Api/Controllers")


def test_file_and_folder_checks_also_read_a_file_list() -> None:
    files = on_files("Api/Controllers/UsersController.cs")

    assert file_exists(files, FileExistsParams(glob="**/*Controller.cs")) == ()
    assert folder_exists(files, FolderExistsParams(path="Controllers")) == ()
    assert folder_exists(files, FolderExistsParams(path="Models")) != ()


def test_only_file_name_checks_are_marked_for_file_lists() -> None:
    marked = {d.name for d in CheckRegistry(builtin_checks()).describe() if d.reads_file_list}

    assert marked == {"file_exists", "folder_exists", "project_count"}


def test_the_checker_refuses_a_file_list_for_a_check_that_reads_contents() -> None:
    requirement = REQUIREMENT.model_copy(update={"check": "controller_suffix", "params": {}})
    assignment = ASSIGNMENT.model_copy(update={"requirements": (requirement,)})
    check_input = CheckInput(
        repo_dir=Path("unused"), assignment=assignment, requirement=requirement, files=()
    )

    with pytest.raises(InvalidAssignmentError, match="reads file contents"):
        DeterministicRequirementChecker(CheckRegistry(builtin_checks())).check(check_input)
