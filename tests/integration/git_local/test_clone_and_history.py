"""LocalGitClient against real git: clone, re-run, sha and history."""

from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.domain.errors import RepoUnavailableError
from tests.integration.git_local.conftest import Remote, git

pytestmark = pytest.mark.integration

CRLF_CONTENT = b"line one\r\nline two\r\n"
HEBREW_MESSAGE = "הוספת בקר מוצרים\n\nפרטים נוספים"  # noqa: RUF001


def snapshot(repo: Path) -> dict[str, bytes]:
    """Every file under `repo` except `.git`, with its exact bytes."""
    return {
        path.relative_to(repo).as_posix(): path.read_bytes()
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts
    }


def current_branch(repo: Path) -> str:
    return git("symbolic-ref", "--short", "HEAD", cwd=repo).strip()


def test_fresh_clone_reads_sha_and_log(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    first = remote.commit({"Program.cs": b"// v1\n"}, "Initial commit")
    second = remote.commit({"Program.cs": b"// v2\n"}, HEBREW_MESSAGE)
    destination = workspace / "dana__api"

    client.clone_or_fetch(remote.url, destination)

    assert client.head_sha(destination) == second
    history = client.log(destination)
    assert [commit.sha for commit in history] == [second, first]
    assert history[0].message == HEBREW_MESSAGE
    assert history[0].author_name == "HW Checker Test"
    assert history[0].authored_at.tzinfo is not None
    assert len(second) == 40


def test_clone_leaves_no_staging_directory(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({"a.txt": b"a"}, "c1")

    client.clone_or_fetch(remote.url, workspace / "repo")

    assert [path.name for path in workspace.iterdir()] == ["repo"]


def test_clone_preserves_crlf_bytes_with_global_autocrlf_true(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({"Controllers/ProductsController.cs": CRLF_CONTENT, "lf.txt": b"a\nb\n"}, "c1")
    destination = workspace / "repo"

    client.clone_or_fetch(remote.url, destination)

    assert (destination / "Controllers/ProductsController.cs").read_bytes() == CRLF_CONTENT
    assert (destination / "lf.txt").read_bytes() == b"a\nb\n"
    assert git("config", "core.autocrlf", cwd=destination).strip() == "false"


def test_rerun_moves_to_new_remote_head(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({"a.txt": b"1"}, "c1")
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)
    newer = remote.commit({"a.txt": b"2"}, "c2")

    client.clone_or_fetch(remote.url, destination)

    assert client.head_sha(destination) == newer
    assert (destination / "a.txt").read_bytes() == b"2"


def test_rerun_discards_local_edits_untracked_and_ignored_files(
    client: LocalGitClient, remote: Remote, workspace: Path, tmp_path: Path
) -> None:
    remote.commit({".gitignore": b"bin/\n", "appsettings.json": b"{}\r\n"}, "c1")
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)
    (destination / "appsettings.json").write_bytes(b'{"ConnectionStrings": "patched"}')
    (destination / "untracked.cs").write_bytes(b"// new")
    (destination / "bin").mkdir()
    (destination / "bin" / "Debug.dll").write_bytes(b"\x00")
    git("add", "untracked.cs", cwd=destination)

    client.clone_or_fetch(remote.url, destination)

    fresh = tmp_path / "fresh"
    client.clone_or_fetch(remote.url, fresh)
    assert snapshot(destination) == snapshot(fresh)
    assert client.head_sha(destination) == client.head_sha(fresh)
    assert git("status", "--porcelain", "--ignored", cwd=destination) == ""


def test_rerun_follows_a_changed_default_branch(
    client: LocalGitClient, remote: Remote, workspace: Path, tmp_path: Path
) -> None:
    remote.commit({"a.txt": b"main"}, "on main")
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)
    trunk_head = remote.commit({"a.txt": b"trunk"}, "on trunk", branch="trunk")
    remote.set_default_branch("trunk")

    client.clone_or_fetch(remote.url, destination)

    assert client.head_sha(destination) == trunk_head
    assert current_branch(destination) == "trunk"
    fresh = tmp_path / "fresh"
    client.clone_or_fetch(remote.url, fresh)
    assert snapshot(destination) == snapshot(fresh)
    assert current_branch(fresh) == "trunk"


def test_existing_folder_that_is_not_a_repo_is_left_untouched(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({"a.txt": b"a"}, "c1")
    destination = workspace / "repo"
    destination.mkdir()
    (destination / "notes.txt").write_bytes(b"keep me")

    with pytest.raises(RepoUnavailableError, match="Nothing was deleted"):
        client.clone_or_fetch(remote.url, destination)

    assert snapshot(destination) == {"notes.txt": b"keep me"}


def test_plain_folder_inside_another_repo_is_not_treated_as_a_clone(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({"a.txt": b"a"}, "c1")
    git("init", cwd=workspace)
    destination = workspace / "repo"
    destination.mkdir()
    (destination / "notes.txt").write_bytes(b"keep me")

    with pytest.raises(RepoUnavailableError, match="not a Git working copy"):
        client.clone_or_fetch(remote.url, destination)

    assert snapshot(destination) == {"notes.txt": b"keep me"}


def test_clone_of_a_different_origin_is_left_untouched(
    client: LocalGitClient, remote: Remote, workspace: Path, tmp_path: Path
) -> None:
    remote.commit({"a.txt": b"a"}, "c1")
    other = Remote(tmp_path, name="other")
    other.commit({"b.txt": b"b"}, "other")
    destination = workspace / "repo"
    client.clone_or_fetch(other.url, destination)
    (destination / "local.txt").write_bytes(b"local change")

    with pytest.raises(RepoUnavailableError, match=r"not .*remote\.git"):
        client.clone_or_fetch(remote.url, destination)

    assert (destination / "local.txt").read_bytes() == b"local change"
    assert (destination / "b.txt").exists()


def test_empty_remote_raises_and_leaves_nothing_behind(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    with pytest.raises(RepoUnavailableError, match="no commits yet"):
        client.clone_or_fetch(remote.url, workspace / "repo")

    assert list(workspace.iterdir()) == []


def test_missing_remote_raises_repo_unavailable(
    client: LocalGitClient, workspace: Path, tmp_path: Path
) -> None:
    missing = (tmp_path / "missing.git").as_uri()

    with pytest.raises(RepoUnavailableError):
        client.clone_or_fetch(missing, workspace / "repo")

    assert list(workspace.iterdir()) == []


def test_head_sha_of_a_plain_folder_raises(client: LocalGitClient, workspace: Path) -> None:
    with pytest.raises(RepoUnavailableError):
        client.head_sha(workspace)
