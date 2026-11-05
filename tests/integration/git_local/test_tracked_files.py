"""LocalGitClient.tracked_files against real git: git decides what `.gitignore` covers."""

from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from tests.integration.git_local.conftest import Remote, git

pytestmark = pytest.mark.integration


def commit_forced(remote: Remote, files: dict[str, bytes]) -> None:
    """Commit `files` even where a `.gitignore` already lists them (as students do)."""
    for name, content in files.items():
        path = remote.author / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        git("add", "--force", "--", name, cwd=remote.author)
    git("-c", "commit.gpgsign=false", "commit", "-m", "Add files", cwd=remote.author)
    git("push", "origin", "main:main", cwd=remote.author)


def test_gitignore_in_a_subfolder_covers_its_tracked_build_output(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    git("checkout", "-B", "main", cwd=remote.author)
    commit_forced(
        remote,
        {
            "Api/.gitignore": b"bin/\n",
            "Api/Api.csproj": b"<Project />\n",
            "Api/bin/Debug/Api.dll": b"x",
            "Api/obj/project.assets.json": b"{}",
            "Other/bin/tool.dll": b"x",
        },
    )
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)

    tracked = client.tracked_files(destination)

    assert "Api/obj/project.assets.json" in tracked.paths
    assert tracked.ignored == ("Api/bin/Debug/Api.dll",)


def test_the_machines_global_excludes_do_not_count(
    client: LocalGitClient, remote: Remote, workspace: Path, tmp_path: Path
) -> None:
    excludes = tmp_path / "global-excludes"
    excludes.write_text("*.user\n", encoding="utf-8")
    git("checkout", "-B", "main", cwd=remote.author)
    commit_forced(remote, {"Api/Api.csproj.user": b"<Project />\n"})
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)
    git("config", "core.excludesFile", excludes.as_posix(), cwd=destination)

    tracked = client.tracked_files(destination)

    assert tracked.paths == ("Api/Api.csproj.user",)
    assert tracked.ignored == ()
