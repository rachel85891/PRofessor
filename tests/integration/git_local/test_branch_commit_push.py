"""LocalGitClient against real git: branch, commit and push."""

from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.domain.errors import GitOperationError, PublishError
from hw_checker.ports import GitClient
from tests.integration.git_local.conftest import Remote, git, git_bytes

pytestmark = pytest.mark.integration

FIX_BRANCH = "hw-review/hw03-2026-10-20"


@pytest.fixture
def clone(client: LocalGitClient, remote: Remote, workspace: Path) -> Path:
    remote.commit({"Program.cs": b"// v1\r\n", "Other.cs": b"// other\n"}, "Initial commit")
    destination = workspace / "repo"
    client.clone_or_fetch(remote.url, destination)
    return destination


def test_client_satisfies_the_port(client: LocalGitClient) -> None:
    port: GitClient = client

    assert port is client


def test_create_branch_checks_it_out_at_the_start_point(
    client: LocalGitClient, clone: Path
) -> None:
    start = client.head_sha(clone)

    client.create_branch(clone, FIX_BRANCH, start)

    assert git("symbolic-ref", "--short", "HEAD", cwd=clone).strip() == FIX_BRANCH
    assert client.head_sha(clone) == start


@pytest.mark.parametrize("name", ["bad..name", "with space", "ends.lock", "-leading-dash", ""])
def test_invalid_branch_name_is_rejected(client: LocalGitClient, clone: Path, name: str) -> None:
    with pytest.raises(GitOperationError, match="invalid branch name"):
        client.create_branch(clone, name, client.head_sha(clone))


def test_existing_branch_is_never_overwritten(client: LocalGitClient, clone: Path) -> None:
    start = client.head_sha(clone)
    client.create_branch(clone, FIX_BRANCH, start)
    (clone / "Program.cs").write_bytes(b"// fixed\r\n")
    fixed = client.commit(clone, "fix", ("Program.cs",))

    with pytest.raises(GitOperationError, match="already exists"):
        client.create_branch(clone, FIX_BRANCH, start)

    assert git("rev-parse", FIX_BRANCH, cwd=clone).strip() == fixed


def test_unknown_start_point_is_rejected(client: LocalGitClient, clone: Path) -> None:
    with pytest.raises(GitOperationError, match="not a commit"):
        client.create_branch(clone, FIX_BRANCH, "0" * 40)


def test_commit_with_nothing_to_commit_raises(client: LocalGitClient, clone: Path) -> None:
    with pytest.raises(GitOperationError, match="nothing to commit"):
        client.commit(clone, "fix", ("Program.cs",))


def test_commit_stages_only_given_paths_with_configured_author(
    client: LocalGitClient, clone: Path
) -> None:
    client.create_branch(clone, FIX_BRANCH, client.head_sha(clone))
    (clone / "Program.cs").write_bytes(b"// fixed\r\n")
    (clone / "Other.cs").write_bytes(b"// changed but not part of the fix\n")

    sha = client.commit(clone, "fix: hw03-r2 return NotFound", ("Program.cs",))

    assert sha == client.head_sha(clone)
    assert len(sha) == 40
    changed = git("show", "--name-only", "--format=", sha, cwd=clone).split()
    assert changed == ["Program.cs"]
    author = git("show", "-s", "--format=%an <%ae>|%cn", sha, cwd=clone).strip()
    assert author == "HW Checker Bot <bot@example.invalid>|HW Checker Bot"
    assert git("config", "--local", "--get-regexp", "^user\\.", cwd=clone, check=False) == ""
    assert git_bytes("show", f"{sha}:Program.cs", cwd=clone) == b"// fixed\r\n"


def test_commit_and_push_creates_the_branch_on_the_remote(
    client: LocalGitClient, clone: Path, remote: Remote
) -> None:
    client.create_branch(clone, FIX_BRANCH, client.head_sha(clone))
    (clone / "Program.cs").write_bytes(b"// fixed\r\n")
    sha = client.commit(clone, "fix", ("Program.cs",))

    client.push(clone, FIX_BRANCH, remote.url)

    assert remote.branch_sha(FIX_BRANCH) == sha


def test_non_fast_forward_push_is_rejected(
    client: LocalGitClient, clone: Path, remote: Remote
) -> None:
    remote.commit({"Program.cs": b"// someone else\n"}, "diverging", branch=FIX_BRANCH)
    theirs = remote.branch_sha(FIX_BRANCH)
    client.create_branch(clone, FIX_BRANCH, client.head_sha(clone))
    (clone / "Program.cs").write_bytes(b"// fixed\r\n")
    client.commit(clone, "fix", ("Program.cs",))

    with pytest.raises(PublishError):
        client.push(clone, FIX_BRANCH, remote.url)

    assert remote.branch_sha(FIX_BRANCH) == theirs
