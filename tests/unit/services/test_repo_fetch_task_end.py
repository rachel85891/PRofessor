"""The fetch service checks the last commit before the next task started (`ends_when`)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hw_checker.checks.deterministic.builtin import builtin_checks
from hw_checker.checks.deterministic.checker import DeterministicRequirementChecker
from hw_checker.checks.registry import CheckRegistry
from hw_checker.domain import (
    Assignment,
    CheckedCommit,
    CommitChoice,
    CommitInfo,
    FetchedRepo,
    Requirement,
    RequirementKind,
    Severity,
    TaskEnd,
)
from hw_checker.domain.errors import InvalidAssignmentError, RepoUnavailableError
from hw_checker.services.repo_fetch import RepoFetchService
from tests.fakes.git import FakeGitClient
from tests.unit.domain import builders

REPO = builders.repo_target()
START = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
ONE_PROJECT = ("Api/Api.csproj", "Api/Program.cs")
TWO_PROJECTS = (*ONE_PROJECT, "Entities/Entities.csproj")
CHECKER = DeterministicRequirementChecker(CheckRegistry(builtin_checks()))
BUILD = Requirement(
    id="hw01-build", title="Builds", severity=Severity.CRITICAL, kind=RequirementKind.BUILD
)


def hw01(ends_when: TaskEnd | None) -> Assignment:
    return Assignment(id="hw01", order=1, title="Users", requirements=(BUILD,), ends_when=ends_when)


TWO_PROJECTS_MARK = hw01(TaskEnd(check="project_count", params={"min": 2}))


def sha(number: int) -> str:
    return f"{number:040x}"


def history(*trees: tuple[str, ...]) -> list[tuple[CommitInfo, tuple[str, ...]]]:
    """A synthetic first-parent history, oldest first: commit N has the files `trees[N-1]`."""
    return [
        (
            CommitInfo(
                sha=sha(number),
                author_name="Student",
                authored_at=START + timedelta(days=number),
                message=f"commit {number}",
            ),
            files,
        )
        for number, files in enumerate(trees, start=1)
    ]


def fetch(git: FakeGitClient, tmp_path: Path, assignment: Assignment | None) -> FetchedRepo:
    return RepoFetchService(git, tmp_path / "clones", CHECKER).fetch(REPO, assignment)


def test_without_ends_when_the_head_is_checked_and_history_is_not_read(tmp_path: Path) -> None:
    git = FakeGitClient(sha=sha(4), history=history(ONE_PROJECT, TWO_PROJECTS))

    fetched = fetch(git, tmp_path, hw01(None))

    assert (fetched.commit_sha, fetched.checked) == (sha(4), None)
    assert git.checked_out == []


def test_no_marker_checks_the_latest_commit(tmp_path: Path) -> None:
    git = FakeGitClient(sha=sha(3), history=history(ONE_PROJECT, ONE_PROJECT, ONE_PROJECT))

    fetched = fetch(git, tmp_path, TWO_PROJECTS_MARK)

    assert fetched.commit_sha == sha(3)
    assert fetched.checked == CheckedCommit(
        sha=sha(3),
        authored_at=START + timedelta(days=3),
        choice=CommitChoice.NEXT_TASK_NOT_STARTED,
        latest_sha=sha(3),
    )
    assert git.checked_out == []


def test_a_marker_at_commit_3_checks_commit_2(tmp_path: Path) -> None:
    trees = history(ONE_PROJECT, ONE_PROJECT, TWO_PROJECTS, TWO_PROJECTS, TWO_PROJECTS)
    git = FakeGitClient(sha=sha(5), history=trees)

    fetched = fetch(git, tmp_path, TWO_PROJECTS_MARK)

    assert fetched.commit_sha == sha(2)
    assert fetched.checked == CheckedCommit(
        sha=sha(2),
        authored_at=START + timedelta(days=2),
        choice=CommitChoice.BEFORE_NEXT_TASK,
        next_task_sha=sha(3),
        later_commits=3,
        latest_sha=sha(5),
    )
    assert fetched.latest_sha == sha(5)
    assert git.checked_out == [(tmp_path / "clones" / REPO.repo_id, sha(2))]


def test_a_marker_at_the_first_commit_checks_the_latest_and_says_why(tmp_path: Path) -> None:
    git = FakeGitClient(sha=sha(2), history=history(TWO_PROJECTS, TWO_PROJECTS))

    fetched = fetch(git, tmp_path, TWO_PROJECTS_MARK)

    assert fetched.commit_sha == sha(2)
    assert fetched.checked is not None
    assert fetched.checked.choice is CommitChoice.NEXT_TASK_AT_FIRST_COMMIT
    assert fetched.checked.next_task_sha == sha(1)
    assert git.checked_out == []


def test_projects_under_bin_and_obj_are_not_a_marker(tmp_path: Path) -> None:
    copied = (*ONE_PROJECT, "Api/bin/Debug/Copy.csproj", "obj/Other.csproj")
    git = FakeGitClient(sha=sha(2), history=history(ONE_PROJECT, copied))

    fetched = fetch(git, tmp_path, TWO_PROJECTS_MARK)

    assert fetched.checked is not None
    assert fetched.checked.choice is CommitChoice.NEXT_TASK_NOT_STARTED


def test_a_check_that_reads_file_contents_cannot_be_an_ends_when(tmp_path: Path) -> None:
    assignment = hw01(TaskEnd(check="controller_suffix"))
    git = FakeGitClient(sha=sha(1), history=history(ONE_PROJECT))

    with pytest.raises(InvalidAssignmentError, match="cannot run on a commit's file list"):
        fetch(git, tmp_path, assignment)


def test_a_history_that_does_not_end_at_the_head_is_refused(tmp_path: Path) -> None:
    git = FakeGitClient(sha=sha(9), history=history(ONE_PROJECT))

    with pytest.raises(RepoUnavailableError, match="does not end at the head"):
        fetch(git, tmp_path, TWO_PROJECTS_MARK)
