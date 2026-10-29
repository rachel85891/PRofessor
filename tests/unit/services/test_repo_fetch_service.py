from pathlib import Path

import pytest

from hw_checker.domain import FetchedRepo
from hw_checker.domain.errors import RepoUnavailableError
from hw_checker.services.repo_fetch import RepoFetchService
from tests.fakes.git import DEFAULT_SHA, FakeGitClient
from tests.unit.domain import builders

REPO = builders.repo_target()


def test_fetch_clones_into_the_repo_folder_and_reads_the_sha(tmp_path: Path) -> None:
    git = FakeGitClient()

    fetched = RepoFetchService(git, tmp_path / "clones").fetch(REPO)

    assert fetched == FetchedRepo(
        working_copy=tmp_path / "clones" / "dana-api", commit_sha=DEFAULT_SHA
    )
    assert git.cloned == [(REPO.url, tmp_path / "clones" / "dana-api")]


def test_fetch_reuses_the_same_folder_across_rounds(tmp_path: Path) -> None:
    git = FakeGitClient()
    service = RepoFetchService(git, tmp_path)

    first = service.fetch(REPO)
    second = service.fetch(REPO)

    assert first.working_copy == second.working_copy
    assert len(git.cloned) == 2


def test_fetch_raises_when_the_repo_is_unavailable(tmp_path: Path) -> None:
    git = FakeGitClient(errors={REPO.url: RepoUnavailableError("repository not found")})

    with pytest.raises(RepoUnavailableError, match="not found"):
        RepoFetchService(git, tmp_path).fetch(REPO)


def test_fetch_raises_when_the_working_copy_cannot_be_read(tmp_path: Path) -> None:
    class NoWorkingCopy(FakeGitClient):
        def clone_or_fetch(self, url: str, destination: Path) -> None:
            self.cloned.append((url, destination))

    with pytest.raises(RepoUnavailableError):
        RepoFetchService(NoWorkingCopy(), tmp_path).fetch(REPO)
