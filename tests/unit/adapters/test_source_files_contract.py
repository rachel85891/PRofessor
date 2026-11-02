"""SourceFiles: the local adapter and the fake behave the same."""

from pathlib import Path

import pytest

from hw_checker.adapters.local_files import LocalSourceFiles
from hw_checker.domain.errors import FixError
from hw_checker.ports import SourceFiles
from tests.fakes.files import FakeSourceFiles

TREE = {
    "Program.cs": b"var app = 1;\n",
    "Controllers/UsersController.cs": b"class UsersController {}\n",
    "bin/Debug/app.dll": b"\x00",
    "obj/project.assets.json": b"{}",
}
SKIP = frozenset({"bin", "obj"})


@pytest.fixture(params=["local", "fake"])
def files(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[SourceFiles, Path]:
    root = tmp_path / "repo"
    if request.param == "fake":
        return FakeSourceFiles({root: TREE}), root
    for path, data in TREE.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    return LocalSourceFiles(), root


def test_files_are_listed_sorted_without_skipped_folders(files: tuple[SourceFiles, Path]) -> None:
    source, root = files

    assert source.list_files(root, SKIP) == ("Controllers/UsersController.cs", "Program.cs")


def test_a_file_is_read_and_written_by_its_relative_path(files: tuple[SourceFiles, Path]) -> None:
    source, root = files

    source.write_bytes(root, "Program.cs", b"var app = 2;\n")

    assert source.read_bytes(root, "Program.cs") == b"var app = 2;\n"


def test_writing_never_adds_a_file(files: tuple[SourceFiles, Path]) -> None:
    source, root = files

    with pytest.raises(FixError, match="never adds files"):
        source.write_bytes(root, "New.cs", b"")


def test_a_missing_file_cannot_be_read(files: tuple[SourceFiles, Path]) -> None:
    source, root = files

    with pytest.raises(FixError):
        source.read_bytes(root, "Missing.cs")


def test_a_missing_root_cannot_be_listed(files: tuple[SourceFiles, Path], tmp_path: Path) -> None:
    source, _ = files

    with pytest.raises(FixError):
        source.list_files(tmp_path / "nowhere", SKIP)


def test_the_local_adapter_never_leaves_the_root(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "secret.txt").write_text("x")

    with pytest.raises(FixError, match="outside the working copy"):
        LocalSourceFiles().read_bytes(tmp_path / "repo", "../secret.txt")
