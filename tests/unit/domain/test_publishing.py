from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.domain import GateDecision, PublishOutcome, PublishRecord


def record(**overrides: Any) -> PublishRecord:
    values: dict[str, Any] = {
        "repo_run_id": "run-1/dana-api",
        "repo_id": "dana-api",
        "branch_name": "hw-review/hw01-2026-11-01",
        "fork": "review-bot/check_dana",
        "pr_url": "https://github.com/dana/api/pull/3",
        "pr_number": 3,
        "published_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        "outcome": PublishOutcome.PUBLISHED,
    }
    return PublishRecord.model_validate(values | overrides)


def test_publish_outcomes() -> None:
    assert {outcome.value for outcome in PublishOutcome} == {
        "published",
        "updated",
        "stale",
        "failed",
    }


@pytest.mark.parametrize("outcome", [PublishOutcome.PUBLISHED, PublishOutcome.UPDATED])
def test_a_published_record_has_its_pull_request(outcome: PublishOutcome) -> None:
    assert record(outcome=outcome).pr_number == 3


@pytest.mark.parametrize("outcome", [PublishOutcome.PUBLISHED, PublishOutcome.UPDATED])
def test_a_published_record_needs_a_pull_request(outcome: PublishOutcome) -> None:
    with pytest.raises(ValidationError, match="has a pull request"):
        record(outcome=outcome, pr_url=None, pr_number=None)


@pytest.mark.parametrize("outcome", [PublishOutcome.STALE, PublishOutcome.FAILED])
def test_a_record_that_was_not_published_has_no_pull_request(outcome: PublishOutcome) -> None:
    with pytest.raises(ValidationError, match="others have none"):
        record(outcome=outcome)

    assert record(outcome=outcome, pr_url=None, pr_number=None).message == ""


def test_a_publish_record_needs_an_aware_time() -> None:
    with pytest.raises(ValidationError):
        record(published_at=datetime(2026, 11, 1, 9, 0))


def test_an_empty_gate_decision_leaves_every_repo_pending() -> None:
    assert GateDecision() == GateDecision(approve=(), decline=(), notes={})


def test_a_gate_decision_refuses_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        GateDecision.model_validate({"aprove": ["dana-api"]})
