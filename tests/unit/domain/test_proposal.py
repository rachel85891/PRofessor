from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hw_checker.domain import (
    FixProposal,
    Patch,
    ProposalStatus,
    ReviewDecision,
    ReviewOutcome,
)
from hw_checker.domain.errors import ReviewError
from tests.unit.domain import builders


def patch(**overrides: Any) -> Patch:
    values: dict[str, Any] = {
        "file_path": "Controllers/ProductsController.cs",
        "search": "return Ok(product);",
        "replace": "return product is null ? NotFound() : Ok(product);",
        "requirement_id": "hw03-r2",
        "explanation": "Return 404 when the product does not exist.",
    }
    return Patch.model_validate(values | overrides)


def test_patch_builds() -> None:
    assert patch().requirement_id == "hw03-r2"


def test_patch_may_delete_text() -> None:
    assert patch(replace="").replace == ""


@pytest.mark.parametrize("file_path", ["/abs.cs", "C:\\a.cs", "D:/a.cs", "../a.cs", "a\\b.cs"])
def test_patch_rejects_unsafe_paths(file_path: str) -> None:
    with pytest.raises(ValidationError):
        patch(file_path=file_path)


def test_patch_rejects_blank_search() -> None:
    with pytest.raises(ValidationError):
        patch(search="  ")


def test_patch_rejects_no_op_replacement() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        patch(replace="return Ok(product);")


def test_patch_rejects_blank_explanation() -> None:
    with pytest.raises(ValidationError):
        patch(explanation="")


def proposal(**overrides: Any) -> FixProposal:
    values: dict[str, Any] = {
        "proposal_id": "p-1",
        "repo_run_id": "run-1/dana-api",
        "repo": builders.repo_target(),
        "branch_name": "hw-review/hw03-2026-10-20",
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "local_repo_dir": Path("workspace/clones/dana-api"),
        "pr_title": "HW3 review: small fixes and questions",
        "pr_body": "Your API builds and runs.",
        "patches": (patch(),),
        "verified": True,
    }
    return FixProposal.model_validate(values | overrides)


def test_fix_proposal_defaults_to_pending_review() -> None:
    assert proposal().status is ProposalStatus.PENDING_REVIEW


@pytest.mark.parametrize("field", ["base_sha", "head_sha", "pr_title", "pr_body"])
def test_fix_proposal_rejects_blank_publishing_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        proposal(**{field: " "})


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (ProposalStatus.PENDING_REVIEW, ProposalStatus.APPROVED),
        (ProposalStatus.PENDING_REVIEW, ProposalStatus.REJECTED),
        (ProposalStatus.APPROVED, ProposalStatus.PUBLISHED),
        (ProposalStatus.APPROVED, ProposalStatus.PENDING_REVIEW),
    ],
)
def test_proposal_moves_along_its_lifecycle(start: ProposalStatus, end: ProposalStatus) -> None:
    assert proposal(status=start).moved_to(end).status is end


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (ProposalStatus.PENDING_REVIEW, ProposalStatus.PUBLISHED),
        (ProposalStatus.REJECTED, ProposalStatus.APPROVED),
        (ProposalStatus.PUBLISHED, ProposalStatus.PENDING_REVIEW),
        (ProposalStatus.APPROVED, ProposalStatus.REJECTED),
    ],
)
def test_proposal_refuses_other_moves(start: ProposalStatus, end: ProposalStatus) -> None:
    with pytest.raises(ReviewError, match=f"is {start.value}; it cannot become {end.value}"):
        proposal(status=start).moved_to(end)


def test_proposal_statuses() -> None:
    assert {status.value for status in ProposalStatus} == {
        "pending_review",
        "approved",
        "rejected",
        "published",
    }


def test_fix_proposal_rejects_blank_branch_name() -> None:
    with pytest.raises(ValidationError):
        proposal(branch_name=" ")


def test_review_decision_builds() -> None:
    decision = ReviewDecision(repo_run_id="run-1/dana-api", outcome=ReviewOutcome.EDITED)

    assert decision.reviewer_notes == ""


def test_review_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate({"repo_run_id": "run-1/dana-api", "outcome": "maybe"})
