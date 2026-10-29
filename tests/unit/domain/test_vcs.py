from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from hw_checker.domain import CommitInfo, FetchedRepo, PullRequestRef

AUTHORED_AT = datetime(2026, 10, 19, 21, 30, tzinfo=UTC)


def test_commit_info_builds() -> None:
    commit = CommitInfo(sha="0f6db0d", author_name="Dana", authored_at=AUTHORED_AT, message="hw3")

    assert commit.sha == "0f6db0d"


def test_commit_info_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        CommitInfo(
            sha="0f6db0d",
            author_name="Dana",
            authored_at=datetime(2026, 10, 19, 21, 30),
            message="hw3",
        )


def test_commit_info_rejects_empty_sha() -> None:
    with pytest.raises(ValidationError):
        CommitInfo(sha="", author_name="Dana", authored_at=AUTHORED_AT, message="hw3")


def test_pull_request_ref_builds() -> None:
    ref = PullRequestRef(number=3, url="https://github.com/dana/api/pull/3")

    assert ref.number == 3


def test_pull_request_ref_rejects_number_below_one() -> None:
    with pytest.raises(ValidationError):
        PullRequestRef(number=0, url="https://github.com/dana/api/pull/0")


def test_fetched_repo_requires_a_commit_sha() -> None:
    assert FetchedRepo(working_copy=Path("clones/dana-api"), commit_sha="0f6db0d").commit_sha

    with pytest.raises(ValidationError):
        FetchedRepo(working_copy=Path("clones/dana-api"), commit_sha="")
