"""Repo fetch service: keeps a local working copy of each student repo."""

import logging
from pathlib import Path

from hw_checker.domain import FetchedRepo, RepoTarget
from hw_checker.ports import GitClient

logger = logging.getLogger(__name__)


class RepoFetchService:
    """Clones or updates a repo in `<clones_dir>/<repo_id>`, reused across rounds."""

    def __init__(self, git: GitClient, clones_dir: Path) -> None:
        self._git = git
        self._clones_dir = clones_dir

    def fetch(self, repo: RepoTarget) -> FetchedRepo:
        """Bring the repo's working copy to the remote head and return it with its sha.

        Raises:
            RepoUnavailableError: the repo cannot be cloned, fetched or read.
            ExecutionEnvironmentError: the git executable is not available.
        """
        working_copy = self._clones_dir / repo.repo_id
        self._git.clone_or_fetch(repo.url, working_copy)
        commit_sha = self._git.head_sha(working_copy)
        logger.info("Repo fetched at commit %s", commit_sha[:12])
        return FetchedRepo(working_copy=working_copy, commit_sha=commit_sha)
