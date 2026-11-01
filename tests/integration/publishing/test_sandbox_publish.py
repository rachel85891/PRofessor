"""A full publish through PublishService with the real GitHub adapter and git, to a sandbox.

Opt-in: skipped unless HW_CHECKER_SANDBOX_REPO names the developer's public sandbox repo
(`owner/name`, never a student's) and HW_CHECKER_GITHUB_TOKEN is set. It pushes a branch
with one harmless commit to the publishing account's fork of the sandbox, opens a pull
request on the sandbox, then publishes a second commit and checks that the same pull
request was updated, not duplicated.
"""

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hw_checker.adapters.git_local import LocalGitClient
from hw_checker.adapters.storage_sqlite import SqlStorage, migrate
from hw_checker.adapters.system_clock import SystemClock
from hw_checker.bootstrap import build_code_host, build_publishing_git
from hw_checker.config.settings import Settings, load_settings
from hw_checker.domain import (
    FixProposal,
    ProposalStatus,
    PublishOutcome,
    RepoRunResult,
    RepoRunStatus,
    RepoTarget,
    Run,
    RunStatus,
    Student,
)
from hw_checker.reporting.writer import MarkdownReportWriter
from hw_checker.services.publishing import PublishService

pytestmark = pytest.mark.integration

SANDBOX_VARIABLE = "HW_CHECKER_SANDBOX_REPO"
RUN_ID = "sandbox-run"
NOTE_FILE = "REVIEW-SANDBOX.md"


def sandbox_repo() -> RepoTarget:
    full_name = os.environ.get(SANDBOX_VARIABLE, "").strip()
    if not full_name:
        pytest.skip(f"{SANDBOX_VARIABLE} is not set")
    owner, _, name = full_name.partition("/")
    # A guard against publishing to a real student's repo by mistake.
    assert owner and "sandbox" in name.lower(), f"{full_name} does not look like a sandbox"
    url = f"https://github.com/{owner}/{name}"
    return RepoTarget(
        repo_id=f"{owner}__{name}".lower(),
        owner=owner,
        name=name,
        url=url,
        student=Student(name="Sandbox Student", github_url=url),
        pair_id=f"{owner}__{name}".lower(),
    )


def commit_note(git: LocalGitClient, clone: Path, text: str) -> str:
    (clone / NOTE_FILE).write_text(f"{text}\n", encoding="utf-8")
    return git.commit(clone, f"docs: {text}", (NOTE_FILE,))


def proposal(
    proposal_id: str, repo: RepoTarget, branch: str, base: str, head: str, clone: Path
) -> FixProposal:
    return FixProposal(
        proposal_id=proposal_id,
        repo_run_id=f"{RUN_ID}/{repo.repo_id}",
        repo=repo,
        branch_name=branch,
        base_sha=base,
        head_sha=head,
        local_repo_dir=clone,
        pr_title="Sandbox review: publishing test",
        pr_body="A test of HW Checker's publishing (stage 13a). Nothing to do here.",
        verified=True,
        status=ProposalStatus.APPROVED,
    )


def store_run(storage: SqlStorage, repo: RepoTarget, base: str) -> None:
    storage.runs.save(
        Run(
            run_id=RUN_ID,
            assignment_id="hw01",
            started_at=datetime.now(UTC),
            status=RunStatus.COMPLETED,
            repo_runs=(
                RepoRunResult(
                    repo_run_id=f"{RUN_ID}/{repo.repo_id}",
                    repo=repo,
                    commit_sha=base,
                    status=RepoRunStatus.DONE,
                ),
            ),
        )
    )


def require_publishing_settings(settings: Settings) -> None:
    if settings.github_token is None:
        pytest.skip("HW_CHECKER_GITHUB_TOKEN is not set")
    assert settings.git.commit_author_name and settings.git.commit_author_email, (
        "Set HW_CHECKER_GIT__COMMIT_AUTHOR_NAME and _EMAIL to the publishing account's identity"
    )


def test_publishing_opens_then_updates_one_pull_request_on_the_sandbox(tmp_path: Path) -> None:
    repo = sandbox_repo()
    settings = load_settings()
    require_publishing_settings(settings)
    code_host = build_code_host(settings)
    git = build_publishing_git(settings)
    clone = tmp_path / repo.repo_id
    git.clone_or_fetch(repo.url, clone)
    base = git.head_sha(clone)
    assert code_host.default_branch_head(repo).sha == base
    branch = f"hw-review/sandbox-{datetime.now(UTC):%Y%m%d-%H%M%S}"
    git.create_branch(clone, branch, base)
    url = f"sqlite:///{(tmp_path / 'hw.db').as_posix()}"
    migrate(url)
    storage = SqlStorage(url)
    try:
        store_run(storage, repo, base)
        writer = MarkdownReportWriter(tmp_path / "runs")
        service = PublishService(storage.proposals, code_host, git, writer, SystemClock(), True)
        first_head = commit_note(git, clone, "first publishing test commit")
        first = service.publish(proposal("p-1", repo, branch, base, first_head, clone))
        second_head = commit_note(git, clone, "second publishing test commit")
        second = service.publish(proposal("p-2", repo, branch, base, second_head, clone))
    finally:
        storage.close()

    print(f"\nPull request: {first.pr_url} ({first.outcome.value}, then {second.outcome.value})")
    assert first.outcome is PublishOutcome.PUBLISHED, first.message
    assert second.outcome is PublishOutcome.UPDATED, second.message
    assert second.pr_number == first.pr_number
    assert settings.github_token is not None
    config = subprocess.run(
        ["git", "config", "--list", "--show-origin"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert settings.github_token.get_secret_value() not in config
