import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

from hw_checker.adapters.docker_runner.archive import build_work_archive

UID = GID = 1654


def write(root: Path, relative: str, content: str = "x") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode())  # bytes: write_text would turn \n into \r\n on Windows


def members(data: bytes) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {member.name: member for member in archive.getmembers()}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root, "Api.csproj", "<Project />")
    write(root, "Controllers/ProductsController.cs", "class C {}\r\n")
    write(root, "bin/Debug/Api.dll")
    write(root, "obj/project.assets.json")
    write(root, ".git/HEAD")
    write(root, "Nested/obj/x.cs")
    return root


def test_excluded_folders_are_left_out(repo: Path) -> None:
    names = set(members(build_work_archive(repo, UID, GID).data))

    assert names == {
        "src",
        "out",
        "src/Api.csproj",
        "src/Controllers",
        "src/Controllers/ProductsController.cs",
        "src/Nested",
    }


def test_ownership_and_modes_are_normalized(repo: Path) -> None:
    for member in members(build_work_archive(repo, UID, GID).data).values():
        assert (member.uid, member.gid, member.uname, member.gname) == (UID, GID, "", "")
        assert member.mtime == 0
        assert member.mode == (0o755 if member.isdir() else 0o644)


def test_file_content_is_kept_byte_for_byte(repo: Path) -> None:
    data = build_work_archive(repo, UID, GID).data

    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        extracted = archive.extractfile("src/Controllers/ProductsController.cs")
        assert extracted is not None
        assert extracted.read() == b"class C {}\r\n"


def test_same_input_gives_the_same_bytes(repo: Path) -> None:
    assert build_work_archive(repo, UID, GID).data == build_work_archive(repo, UID, GID).data


def test_symlink_in_the_repo_is_skipped(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.symlink(outside, repo / "link.txt")
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin rights on Windows")

    archive = build_work_archive(repo, UID, GID)

    assert "src/link.txt" not in members(archive.data)
    assert archive.skipped == ("link.txt",)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
def test_junction_escaping_the_repo_is_skipped(repo: Path, tmp_path: Path) -> None:
    import _winapi

    outside = tmp_path / "outside"
    write(outside, "secret.txt", "secret")
    _winapi.CreateJunction(str(outside), str(repo / "escape"))

    archive = build_work_archive(repo, UID, GID)

    names = members(archive.data)
    assert not any(name.startswith("src/escape") for name in names)
    assert archive.skipped == ("escape",)


def test_member_names_never_escape_the_archive_root(repo: Path) -> None:
    for name in members(build_work_archive(repo, UID, GID).data):
        assert not name.startswith("/")
        assert ".." not in Path(name).parts
        assert name.split("/")[0] in {"src", "out"}
