"""
GitHub write adapter.

Deliberately tiny. It can publish a commit status and read commit statuses
back, and it can do nothing else. There is no merge, no push, no delete, no
branch or repository setting change, no workflow dispatch, and no issue or pull
request edit, so no later mistake can reach one.

No model object is ever given an instance of this class.
"""

from __future__ import annotations

from typing import Any

import requests

from clauseci.adapters.github import API_ROOT, TIMEOUT_SECONDS, GitHubError
from clauseci.settings import Settings


class GitHubWriteError(GitHubError):
    """A GitHub write failed."""


class GitHubStatusWriter:
    """Commit status writes and read back, for one allowlisted repository."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _require_allowed(self, owner: str, repo: str) -> None:
        if f"{owner}/{repo}".lower() != self._settings.allowed_repository.lower():
            raise GitHubWriteError(
                f"{owner}/{repo} is not the allowlisted repository "
                f"({self._settings.allowed_repository})"
            )

    def set_commit_status(
        self,
        owner: str,
        repo: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        """Publish one commit status against one exact commit."""
        self._require_allowed(owner, repo)
        if len(sha) != 40:
            raise GitHubWriteError(f"refusing to write a status against {sha!r}, "
                                   f"which is not a full commit sha")

        body: dict[str, Any] = {
            "state": state,
            "context": context,
            "description": description[:140],
        }
        if target_url:
            body["target_url"] = target_url

        response = self._session.post(
            f"{API_ROOT}/repos/{owner}/{repo}/statuses/{sha}",
            json=body,
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 201:
            raise GitHubWriteError(
                f"status write failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()

    def read_commit_statuses(self, owner: str, repo: str, sha: str) -> list[dict[str, Any]]:
        """Read statuses back from provider state, newest first."""
        self._require_allowed(owner, repo)
        response = self._session.get(
            f"{API_ROOT}/repos/{owner}/{repo}/commits/{sha}/statuses",
            params={"per_page": 100},
            timeout=TIMEOUT_SECONDS,
        )
        if not response.ok:
            raise GitHubWriteError(
                f"status read failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()

    def latest_status_for_context(
        self, owner: str, repo: str, sha: str, context: str
    ) -> dict[str, Any] | None:
        """The most recent status GitHub holds for one context on one commit."""
        for status in self.read_commit_statuses(owner, repo, sha):
            if status.get("context") == context:
                return status
        return None
