"""Repo fetch service: keeps a local working copy of each student repo."""

import logging
from pathlib import Path

from hw_checker.domain import (
    Assignment,
    CheckedCommit,
    CheckInput,
    CommitChoice,
    CommitInfo,
    FetchedRepo,
    RepoTarget,
)
from hw_checker.domain.errors import RepoUnavailableError
from hw_checker.ports import GitClient, RequirementChecker

logger = logging.getLogger(__name__)


class RepoFetchService:
    """Clones or updates a repo in `<clones_dir>/<repo_id>`, reused across rounds, and
    checks out the commit the round checks."""

    def __init__(self, git: GitClient, clones_dir: Path, checker: RequirementChecker) -> None:
        self._git = git
        self._clones_dir = clones_dir
        self._checker = checker

    def fetch(self, repo: RepoTarget, assignment: Assignment | None = None) -> FetchedRepo:
        """Bring the repo's working copy to the remote head, then to the commit to check.

        Without an `ends_when` on `assignment`, the head is checked. With one, the checked
        commit is the last first-parent commit before the first one where `ends_when`
        holds; the head when it never holds, or already holds at the first commit.

        Raises:
            RepoUnavailableError: the repo cannot be cloned, fetched, read or checked out.
            ExecutionEnvironmentError: the git executable is not available.
            InvalidAssignmentError: `ends_when` names an unknown check or invalid params.
        """
        working_copy = self._clones_dir / repo.repo_id
        self._git.clone_or_fetch(repo.url, working_copy)
        latest_sha = self._git.head_sha(working_copy)
        if assignment is None or assignment.ends_when is None:
            logger.info("Repo fetched at commit %s", latest_sha[:12])
            return FetchedRepo(working_copy=working_copy, commit_sha=latest_sha)
        checked = self._choose_commit(working_copy, assignment, latest_sha)
        if checked.sha != latest_sha:
            self._git.check_out(working_copy, checked.sha)
        logger.info(
            "Repo fetched at %s; checking %s (%s, %d later commits)",
            latest_sha[:12],
            checked.sha[:12],
            checked.choice.value,
            checked.later_commits,
            extra={"repo_id": repo.repo_id, "choice": checked.choice.value},
        )
        return FetchedRepo(working_copy=working_copy, commit_sha=checked.sha, checked=checked)

    def _choose_commit(
        self, working_copy: Path, assignment: Assignment, latest_sha: str
    ) -> CheckedCommit:
        history = self._git.first_parent_history(working_copy)
        if not history or history[-1].sha != latest_sha:
            raise RepoUnavailableError(
                f"{working_copy.name}: the history does not end at the head {latest_sha[:12]}"
            )
        start = next(
            (
                index
                for index, commit in enumerate(history)
                if self._next_task_started(working_copy, assignment, commit)
            ),
            None,
        )
        if start is None or start == 0:
            choice = (
                CommitChoice.NEXT_TASK_NOT_STARTED
                if start is None
                else CommitChoice.NEXT_TASK_AT_FIRST_COMMIT
            )
            return CheckedCommit(
                sha=latest_sha,
                authored_at=history[-1].authored_at,
                choice=choice,
                next_task_sha=None if start is None else history[start].sha,
                latest_sha=latest_sha,
            )
        checked = history[start - 1]
        return CheckedCommit(
            sha=checked.sha,
            authored_at=checked.authored_at,
            choice=CommitChoice.BEFORE_NEXT_TASK,
            next_task_sha=history[start].sha,
            later_commits=len(history) - start,
            latest_sha=latest_sha,
        )

    def _next_task_started(
        self, working_copy: Path, assignment: Assignment, commit: CommitInfo
    ) -> bool:
        requirement = assignment.end_requirement
        if requirement is None:
            return False
        check_input = CheckInput(
            repo_dir=working_copy,
            assignment=assignment,
            requirement=requirement,
            files=self._git.tree_files(working_copy, commit.sha),
        )
        # The condition holds when its check passes: no findings and nothing skipped.
        return self._checker.check(check_input) == ()
