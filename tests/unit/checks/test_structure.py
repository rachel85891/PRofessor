import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.checks.deterministic.structure import (
    FileExistsParams,
    FolderExistsParams,
    file_exists,
    folder_exists,
    glob_pattern,
    scan_repo,
)
from hw_checker.domain import (
    Assignment,
    CheckInput,
    FindingSource,
    Requirement,
    RequirementKind,
    Severity,
)

REQUIREMENT = Requirement(
    id="hw01-r1",
    title="ProductsController exists",
    severity=Severity.MAJOR,
    kind=RequirementKind.STRUCTURE,
    check="file_exists",
    params={"glob": "**/Controllers/ProductsController.cs"},
)
ASSIGNMENT = Assignment(id="hw01", order=1, title="Products", requirements=(REQUIREMENT,))


def write(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"class C {}")


def check_input(repo: Path) -> CheckInput:
    return CheckInput(repo_dir=repo, assignment=ASSIGNMENT, requirement=REQUIREMENT)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root, "ShopApi/Controllers/ProductsController.cs")
    write(root, "ShopApi/Program.cs")
    write(root, "ShopApi/Models/Product.cs")
    return root


def test_file_found_passes(repo: Path) -> None:
    params = FileExistsParams(glob="**/Controllers/ProductsController.cs")

    assert file_exists(check_input(repo), params) == ()


def test_file_missing_gives_one_deterministic_finding(repo: Path) -> None:
    (finding,) = file_exists(check_input(repo), FileExistsParams(glob="**/Services/*.cs"))

    assert finding.source is FindingSource.DETERMINISTIC
    assert finding.confidence == 1.0
    assert finding.severity is Severity.MAJOR
    assert finding.evidence is None
    assert (finding.requirement_id, finding.assignment_id) == ("hw01-r1", "hw01")
    assert finding.message_en == "No file matching **/Services/*.cs was found in the repository."
    assert "**/Services/*.cs" in finding.message_he


def test_min_count_counts_matching_files(repo: Path) -> None:
    write(repo, "ShopApi/Controllers/OrdersController.cs")

    assert (
        file_exists(check_input(repo), FileExistsParams(glob="**/*Controller.cs", min_count=2))
        == ()
    )
    (finding,) = file_exists(
        check_input(repo), FileExistsParams(glob="**/*Controller.cs", min_count=3)
    )
    assert (
        finding.message_en == "At least 3 files matching **/*Controller.cs were expected; 2 found."
    )


def test_matching_is_case_insensitive(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "controllers/productscontroller.cs")

    assert (
        file_exists(check_input(repo), FileExistsParams(glob="Controllers/ProductsController.cs"))
        == ()
    )
    assert folder_exists(check_input(repo), FolderExistsParams(path="Controllers")) == ()


def test_files_under_bin_and_obj_are_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write(repo, "bin/Debug/Controllers/ProductsController.cs")
    write(repo, "obj/Controllers/ProductsController.cs")
    write(repo, ".git/Controllers/ProductsController.cs")

    assert (
        len(file_exists(check_input(repo), FileExistsParams(glob="**/ProductsController.cs"))) == 1
    )
    assert len(folder_exists(check_input(repo), FolderExistsParams(path="Controllers"))) == 1


def test_folder_found_at_any_depth(repo: Path) -> None:
    assert folder_exists(check_input(repo), FolderExistsParams(path="Models")) == ()
    assert folder_exists(check_input(repo), FolderExistsParams(path="ShopApi/Models")) == ()


def test_folder_missing_gives_a_finding(repo: Path) -> None:
    (finding,) = folder_exists(check_input(repo), FolderExistsParams(path="Services"))

    assert finding.message_en == "No folder named Services was found in the repository."


def test_folder_name_must_match_a_whole_segment(repo: Path) -> None:
    assert len(folder_exists(check_input(repo), FolderExistsParams(path="Api"))) == 1


def test_symlink_leading_out_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside, "Controllers/ProductsController.cs")
    repo = tmp_path / "repo"
    write(repo, "Program.cs")
    try:
        os.symlink(outside, repo / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights on Windows")

    assert (
        len(file_exists(check_input(repo), FileExistsParams(glob="**/ProductsController.cs"))) == 1
    )
    assert "linked" not in scan_repo(repo).folders


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_junction_leading_out_is_not_followed(tmp_path: Path) -> None:
    import _winapi

    outside = tmp_path / "outside"
    write(outside, "Controllers/ProductsController.cs")
    repo = tmp_path / "repo"
    write(repo, "Program.cs")
    _winapi.CreateJunction(str(outside), str(repo / "escape"))

    assert (
        len(file_exists(check_input(repo), FileExistsParams(glob="**/ProductsController.cs"))) == 1
    )
    assert scan_repo(repo).files == ("Program.cs",)


@pytest.mark.parametrize(
    "glob", ["/etc/passwd", "C:/Windows/*", "../outside/*.cs", "a/../../b", "a\\b.cs", " "]
)
def test_escaping_globs_are_invalid_params(glob: str) -> None:
    with pytest.raises(ValidationError):
        FileExistsParams(glob=glob)


@pytest.mark.parametrize("path", ["../Controllers", "/Controllers", "Contr*", "D:/x"])
def test_invalid_folder_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValidationError):
        FolderExistsParams(path=path)


def test_min_count_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        FileExistsParams(glob="*.cs", min_count=0)


@pytest.mark.parametrize(
    ("glob", "path", "matches"),
    [
        ("**/Controllers/*.cs", "Controllers/A.cs", True),
        ("**/Controllers/*.cs", "src/Api/Controllers/A.cs", True),
        ("**/Controllers/*.cs", "src/Controllers/Sub/A.cs", False),
        ("*.csproj", "Api.csproj", True),
        ("*.csproj", "src/Api.csproj", False),
        ("src/**", "src/a/b/c.cs", True),
        ("Model?.cs", "Models.cs", True),
        ("Model?.cs", "Model/s.cs", False),
        ("a.b", "aXb", False),
    ],
)
def test_glob_semantics(glob: str, path: str, matches: bool) -> None:
    assert bool(glob_pattern(glob).fullmatch(path)) is matches


def test_same_failure_gives_the_same_id_and_a_different_failure_a_different_id(repo: Path) -> None:
    first = file_exists(check_input(repo), FileExistsParams(glob="**/Services/*.cs"))
    again = file_exists(check_input(repo), FileExistsParams(glob="**/Services/*.cs"))
    other = file_exists(check_input(repo), FileExistsParams(glob="**/Data/*.cs"))

    assert first[0].id == again[0].id
    assert first[0].id != other[0].id
    assert first[0].id.startswith("hw01-r1-")
