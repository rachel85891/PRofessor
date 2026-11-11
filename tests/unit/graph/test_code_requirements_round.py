"""A `code` requirement in a round (stage 16): until the code reviewer (stage 8b) is
enabled, it is not evaluated with `awaiting_reviewer`, never a failure, and never reaches
the fixer as a finding."""

from pathlib import Path

from hw_checker.domain import (
    Assignment,
    NotEvaluated,
    NotEvaluatedReason,
    RepoRunStatus,
    RequirementKind,
    Severity,
)
from tests.unit.domain import builders
from tests.unit.graph.harness import CATALOG, REPO_IDS, Harness

CODE = builders.requirement(
    id="hw01-null-check",
    kind=RequirementKind.CODE,
    severity=Severity.MAJOR,
    reviewer_guidance="GET by id returns NotFound when the user is missing.",
)


def _with_code(catalog: tuple[Assignment, ...]) -> tuple[Assignment, ...]:
    return tuple(
        a.model_copy(update={"requirements": (*a.requirements, CODE)}) if a.id == "hw01" else a
        for a in catalog
    )


def test_a_code_requirement_is_awaiting_the_reviewer_and_no_failure(tmp_path: Path) -> None:
    harness = Harness(tmp_path, catalog=_with_code(CATALOG))

    output = harness.run()

    for repo_id in REPO_IDS:
        repo_run = harness.repo_run(repo_id)
        assert repo_run.status is RepoRunStatus.DONE
        hw01 = next(r for r in repo_run.assignment_results if r.assignment_id == "hw01")
        assert (
            NotEvaluated(
                requirement_id="hw01-null-check", reason=NotEvaluatedReason.AWAITING_REVIEWER
            )
            in hw01.not_evaluated
        )
        assert all(f.requirement_id != "hw01-null-check" for f in hw01.findings)
    assert all(line.major == 0 for line in output["summary"].repos)
    assert all(call.requirement.id != "hw01-null-check" for call in harness.checker.checked)
