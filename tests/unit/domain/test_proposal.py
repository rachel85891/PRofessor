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


def test_fix_proposal_defaults_to_pending_review() -> None:
    proposal = FixProposal(
        proposal_id="p-1",
        repo_run_id="run-1/dana-api",
        repo=builders.repo_target(),
        branch_name="hw-review/hw03-2026-10-20",
        patches=(patch(),),
        verified=True,
    )

    assert proposal.status is ProposalStatus.PENDING_REVIEW


def test_proposal_statuses() -> None:
    assert {status.value for status in ProposalStatus} == {
        "pending_review",
        "approved",
        "rejected",
        "published",
    }


def test_fix_proposal_rejects_blank_branch_name() -> None:
    with pytest.raises(ValidationError):
        FixProposal(
            proposal_id="p-1",
            repo_run_id="run-1/dana-api",
            repo=builders.repo_target(),
            branch_name=" ",
            verified=False,
        )


def test_review_decision_builds() -> None:
    decision = ReviewDecision(repo_run_id="run-1/dana-api", outcome=ReviewOutcome.EDITED)

    assert decision.reviewer_notes == ""


def test_review_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate({"repo_run_id": "run-1/dana-api", "outcome": "maybe"})
