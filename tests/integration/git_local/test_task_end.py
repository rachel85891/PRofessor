"""The fetch service with real git: the commit before the next task started (`ends_when`)."""

from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    CommitChoice,
    RepoTarget,
    Requirement,
    RequirementKind,
    Severity,
    TaskEnd,
)
from hw_checker.services.repo_fetch import RepoFetchService
from tests.integration.git_local.conftest import Remote, git
from tests.unit.domain import builders

pytestmark = pytest.mark.integration

API = {"Api/Api.csproj": b"<Project />\n", "Api/Program.cs": b"// v1\n"}
LAYERS = {"Entities/Entities.csproj": b"<Project />\n"}
HW01 = Assignment(
    id="hw01",
    order=1,
    title="Users",
    requirements=(
        Requirement(
            id="hw01-build", title="Builds", severity=Severity.CRITICAL, kind=RequirementKind.BUILD
        ),
    ),
    ends_when=TaskEnd(check="project_count", params={"min": 2}),
)


def service(client: LocalGitClient, workspace: Path) -> RepoFetchService:
    checker = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
    return RepoFetchService(client, workspace, checker)


def repo_for(remote: Remote) -> RepoTarget:
    return builders.repo_target().model_copy(update={"url": remote.url})


def test_no_marker_checks_the_latest_commit(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit(API, "first")
    latest = remote.commit({"Api/Program.cs": b"// v2\n"}, "second")

    fetched = service(client, workspace).fetch(repo_for(remote), HW01)

    assert fetched.commit_sha == latest
    assert fetched.checked is not None
    assert fetched.checked.choice is CommitChoice.NEXT_TASK_NOT_STARTED


def test_a_marker_at_commit_3_checks_out_commit_2(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit(API, "first")
    second = remote.commit({"Api/Program.cs": b"// v2\n"}, "second")
    third = remote.commit(LAYERS, "layers")
    latest = remote.commit({"Api/Program.cs": b"// v4\n"}, "fourth")

    fetched = service(client, workspace).fetch(repo_for(remote), HW01)

    assert fetched.commit_sha == second
    assert fetched.checked is not None
    assert (fetched.checked.next_task_sha, fetched.checked.later_commits) == (third, 2)
    assert fetched.latest_sha == latest
    assert client.head_sha(fetched.working_copy) == second
    assert not (fetched.working_copy / "Entities").exists()
    assert (fetched.working_copy / "Api" / "Program.cs").read_bytes() == b"// v2\n"

    # The next round starts from the remote head again, and a local clone keeps the commit.
    again = service(client, workspace).fetch(repo_for(remote), HW01)
    assert again.commit_sha == second
    copy = workspace.parent / "fix-copy"
    client.clone_local(fetched.working_copy, copy)
    assert client.head_sha(copy) == second


def test_a_marker_at_the_first_commit_checks_the_latest(
    client: LocalGitClient, remote: Remote, workspace: Path
) -> None:
    remote.commit({**API, **LAYERS}, "both projects from the start")
    latest = remote.commit({"Api/Program.cs": b"// v2\n"}, "second")

    fetched = service(client, workspace).fetch(repo_for(remote), HW01)

    assert fetched.commit_sha == latest
    assert fetched.checked is not None
    assert fetched.checked.choice is CommitChoice.NEXT_TASK_AT_FIRST_COMMIT
    assert git("rev-parse", "HEAD", cwd=fetched.working_copy).strip() == latest
