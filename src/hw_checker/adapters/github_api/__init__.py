"""GitHub adapter: implements the CodeHost port over GitHub's REST API."""

from hw_checker.adapters.github_api.client import GitHubCodeHost, GitHubConfig

__all__ = ["GitHubCodeHost", "GitHubConfig"]
