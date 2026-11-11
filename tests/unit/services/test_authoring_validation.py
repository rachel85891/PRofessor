"""The authoring flow's validation (stage 16, design §8.3): the schemas and the catalog,
the intake's coverage, the dry run on `reference/`, the preview on the workspace clones,
and the rule changes. Synthetic data and a fake runner only."""

from pathlib import Path

from hw_checker.domain import ExecutionResult, FailureKind, StepStatus
from hw_checker.domain.authoring import (
    AuthorReply,
    DryRunStatus,
    PreviewStatus,
    ValidationReport,
)
from hw_checker.services.authoring import AuthoringService
from tests.fakes.execution import FakeCodeRunner
from tests.unit.services.authoring_data import (
    build_service,
    make_folders,
    make_repo,
    not_yet_taught_rule,
    post_requirement,
    projects_requirement,
    valid_reply,
    write,
)

ARCHITECTURE_ONLY = {
    "id": "hw02",
    "order": 2,
    "title": "Layers",
    "requirements": {"architecture": [{"text": "At least four projects"}]},
}


def _validate(
    service: AuthoringService, reply: AuthorReply, with_execution: bool = False
) -> ValidationReport:
    return service.validate(service.load_intake("hw02"), reply, with_execution)


def test_a_valid_draft_passes_and_says_the_dry_run_was_skipped(tmp_path: Path) -> None:
    report = _validate(build_service(tmp_path), valid_reply())

    assert report.passed, report.reasons
    assert report.problems == ()
    assert all(item.covered_by for item in report.coverage)
    assert report.dry_run.status is DryRunStatus.SKIPPED
    assert report.preview.status is PreviewStatus.NO_REPOS


def test_every_problem_is_listed_together(tmp_path: Path) -> None:
    reply = AuthorReply(
        requirements=(
            post_requirement(origin={"source": "task_text", "quote": "POST returns 418"}),
            projects_requirement(check="project_counts", intake_items=[]),
            projects_requirement(
                id="hw02-projects-min", params={"minimum": 4}, intake_items=["general.9"]
            ),
        ),
        rules=(not_yet_taught_rule(),),
    )

    report = _validate(build_service(tmp_path), reply)

    assert not report.passed
    reasons = "\n".join(report.reasons)
    assert "requirement 'hw02-post': origin task_text, but its quote is not in task.md" in reasons
    assert "intake_items names 'general.9', which is not an intake item's key" in reasons
    assert "check 'project_counts': unknown check 'project_counts'" in reasons
    assert "param 'minimum': unknown param" in reasons
    assert report.dry_run.status is DryRunStatus.NOT_RUN


def test_an_uncovered_intake_item_fails_with_its_text(tmp_path: Path) -> None:
    reply = AuthorReply(requirements=(post_requirement(), projects_requirement()))

    report = _validate(build_service(tmp_path), reply)

    assert not report.passed
    assert [item.key for item in report.uncovered] == ["not_yet_taught.1"]
    assert report.reasons == (
        "intake item `not_yet_taught.1` is not covered by any requirement, rule or example: "
        "dependency injection",
    )


def test_a_loader_problem_names_the_file_and_the_field(tmp_path: Path) -> None:
    reply = AuthorReply(
        requirements=(post_requirement(depends_on=["hw02-missing"]), projects_requirement()),
        rules=(not_yet_taught_rule(until="hw09"),),
    )

    reasons = "\n".join(_validate(build_service(tmp_path), reply).reasons)

    assert (
        "assignments/hw02/requirements.yaml: requirement 'hw02-post', field 'depends_on': "
        "unknown requirement 'hw02-missing'"
    ) in reasons
    assert "rule 'hw02-not-yet-taught', field 'until': unknown assignment 'hw09'" in reasons


def _architecture_only(root: Path, runner: FakeCodeRunner | None = None) -> AuthoringService:
    make_folders(root, ARCHITECTURE_ONLY)
    return build_service(root, runner=runner)


def test_the_dry_run_passes_on_a_reference_that_meets_every_requirement(tmp_path: Path) -> None:
    runner = FakeCodeRunner()
    service = _architecture_only(tmp_path, runner)
    make_repo(tmp_path / "assignments" / "hw02" / "reference", projects=4)

    report = _validate(service, AuthorReply(requirements=(projects_requirement(),)))

    assert report.dry_run.status is DryRunStatus.PASSED
    assert report.passed
    assert [request.repo_id for request in runner.requests] == ["reference"]


def test_the_dry_run_fails_on_a_broken_reference_and_sends_the_draft_back(
    tmp_path: Path,
) -> None:
    service = _architecture_only(tmp_path)
    make_repo(tmp_path / "assignments" / "hw02" / "reference", projects=1)

    report = _validate(service, AuthorReply(requirements=(projects_requirement(),)))

    assert report.dry_run.status is DryRunStatus.FAILED
    assert report.dry_run.failed("hw02-projects")
    assert not report.passed
    assert report.reasons[0].startswith(
        "fails the reference solution, which must pass it: hw02-projects: "
    )


def test_the_dry_run_is_skipped_without_a_reference_and_needs_no_docker(tmp_path: Path) -> None:
    runner = FakeCodeRunner()
    service = _architecture_only(tmp_path, runner)
    (tmp_path / "assignments" / "hw02" / "reference").mkdir()

    report = _validate(service, AuthorReply(requirements=(projects_requirement(),)))

    assert report.dry_run.status is DryRunStatus.SKIPPED
    assert runner.calls == []


def test_the_preview_counts_repos_and_runs_only_file_checks(tmp_path: Path) -> None:
    runner = FakeCodeRunner()
    service = build_service(tmp_path, runner=runner)
    clones = tmp_path / "workspace" / "clones"
    for name, projects in (("a__api", 4), ("b__api", 4), ("c__api", 1)):
        make_repo(clones / name, projects)

    preview = _validate(service, valid_reply()).preview

    assert preview.status is PreviewStatus.RAN
    assert (preview.repos, preview.errors, preview.with_execution) == (3, 0, False)
    count = preview.count("hw02-projects")
    assert count is not None
    assert (count.failed, count.passed, count.not_evaluated, count.flagged) == (1, 2, 0, False)
    assert preview.left_out == ("hw02-post",)
    assert runner.calls == []


def test_the_preview_with_execution_builds_and_runs_every_clone(tmp_path: Path) -> None:
    runner = FakeCodeRunner()
    service = build_service(tmp_path, runner=runner)
    for name in ("a__api", "b__api"):
        make_repo(tmp_path / "workspace" / "clones" / name, 4)

    preview = _validate(service, valid_reply(), with_execution=True).preview

    assert preview.left_out == ()
    assert preview.count("hw02-post") is not None
    assert sorted(r.repo_id for r in runner.requests) == ["a__api", "b__api"]


def test_a_requirement_that_fails_almost_every_repo_is_flagged(tmp_path: Path) -> None:
    service = build_service(tmp_path, flag_share=0.75)
    clones = tmp_path / "workspace" / "clones"
    for name, projects in (("a__api", 1), ("b__api", 1), ("c__api", 1), ("d__api", 4)):
        make_repo(clones / name, projects)

    report = _validate(service, valid_reply())

    count = report.preview.count("hw02-projects")
    assert count is not None and count.flagged
    assert report.passed  # a flag is for the developer; it never fails the draft


def test_the_preview_never_names_a_repo(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    make_repo(tmp_path / "workspace" / "clones" / "avigail__secret-api", 1)
    intake = service.load_intake("hw02")
    reply = valid_reply()
    report = service.validate(intake, reply)

    path = service.write_report(intake, reply, report, 1, 3)

    text = path.read_text(encoding="utf-8")
    assert "avigail" not in text and "secret" not in text
    assert "1 repos at their current working copy" in text


def test_the_changes_list_new_and_ended_rules(tmp_path: Path) -> None:
    make_folders(tmp_path)
    write(
        tmp_path / "assignments" / "hw01" / "review_rules.yaml",
        {
            "rules": [
                {
                    "id": "hw01-only",
                    "applies_to": ["fixer"],
                    "kind": "never_mention",
                    "text": "Do not mention layers.",
                    "until": "hw01",
                    "origin": {"source": "developer", "quote": "the lecturer"},
                    "status": "approved",
                }
            ]
        },
    )

    changes = _validate(build_service(tmp_path), valid_reply()).changes

    assert changes.new == ("hw02-not-yet-taught",)
    assert changes.ended == ("hw01-only",)
    assert changes.replaced == ()


def test_a_reference_that_does_not_build_is_incomplete_not_passed(tmp_path: Path) -> None:
    failed = ExecutionResult(
        build_status=StepStatus.FAILED,
        run_status=StepStatus.SKIPPED,
        failure_kind=FailureKind.BUILD_FAILED,
    )
    service = build_service(tmp_path, runner=FakeCodeRunner(results={"reference": failed}))
    make_repo(tmp_path / "assignments" / "hw02" / "reference", projects=4)
    intake = service.load_intake("hw02")

    report = service.validate(intake, valid_reply())

    assert report.dry_run.status is DryRunStatus.INCOMPLETE
    assert report.dry_run.failures == ()
    assert report.dry_run.not_evaluated == ("hw02-post: not evaluated (build_failed)",)
    assert report.passed  # not the author's fault: it goes to approval
    text = service.write_report(intake, valid_reply(), report, 1, 3).read_text("utf-8")
    top = text.split("## Drafted items")[0]
    assert "**The dry run is incomplete.** The reference solution could not check these" in top
    assert "- hw02-post: not evaluated (build_failed)" in top


def test_a_reference_that_passes_every_requirement_is_passed(tmp_path: Path) -> None:
    service = build_service(tmp_path)
    make_repo(tmp_path / "assignments" / "hw02" / "reference", projects=4)
    intake = service.load_intake("hw02")
    covered = [
        "requirements.architecture.1",
        "requirements.return_values.1",
        "lecturer_decisions.accepted_alternatives.1",
    ]
    reply = AuthorReply(
        requirements=(projects_requirement(intake_items=covered),),
        rules=(not_yet_taught_rule(),),
    )

    report = service.validate(intake, reply)

    assert report.dry_run.status is DryRunStatus.PASSED
    assert report.dry_run.not_evaluated == ()
    text = service.write_report(intake, reply, report, 1, 3).read_text("utf-8")
    assert "The dry run is incomplete" not in text
