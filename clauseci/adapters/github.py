"""
Read only GitHub adapter.

Every method here issues GET requests. There is no method that writes a status,
a comment, a commit or a merge, so no later mistake can turn a snapshot run
into a mutation.

Code from the analyzed pull request is never checked out and never executed.
File contents are fetched over the API as bytes and treated as data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

from clauseci.settings import Settings

API_ROOT = "https://api.github.com"
TIMEOUT_SECONDS = 30

_PR_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_SHORT = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^#\s]+)#(?P<number>\d+)$")
_BARE = re.compile(r"^#?(?P<number>\d+)$")


class GitHubError(RuntimeError):
    """A GitHub read failed."""


class RepositoryNotAllowed(GitHubError):
    """The reference points at a repository outside the allowlist."""


class PullRequestReferenceError(ValueError):
    """The pull request reference could not be parsed."""


@dataclass(frozen=True)
class PullRequestRef:
    owner: str | None
    repo: str | None
    number: int


def parse_pr_reference(reference: str) -> PullRequestRef:
    """
    Accept a full pull request URL, an owner/repo#number short form, or a bare
    number. A bare number carries no repository, so the caller supplies it.
    """
    text = (reference or "").strip()
    if not text:
        raise PullRequestReferenceError("pull request reference is empty")

    for pattern in (_PR_URL, _SHORT):
        match = pattern.match(text)
        if match:
            return PullRequestRef(
                owner=match.group("owner"),
                repo=match.group("repo").removesuffix(".git"),
                number=int(match.group("number")),
            )

    match = _BARE.match(text)
    if match:
        return PullRequestRef(owner=None, repo=None, number=int(match.group("number")))

    raise PullRequestReferenceError(
        f"could not parse {reference!r}. expected a pull request URL, "
        f"owner/repo#number, or a bare number"
    )


class GitHubReader:
    """Read only access to one allowlisted repository."""

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

    # ---------------------------------------------------------------- allowlist

    def resolve_reference(self, reference: str) -> PullRequestRef:
        """Parse a reference and confirm it points at the allowlisted repository."""
        ref = parse_pr_reference(reference)
        owner = ref.owner or self._settings.github_owner
        repo = ref.repo or self._settings.github_repo
        target = f"{owner}/{repo}"
        if target.lower() != self._settings.allowed_repository.lower():
            raise RepositoryNotAllowed(
                f"{target} is not the allowlisted repository "
                f"({self._settings.allowed_repository})"
            )
        return PullRequestRef(owner=owner, repo=repo, number=ref.number)

    # ------------------------------------------------------------------- reads

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}")

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}")

    def list_changed_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """
        Complete changed file inventory, paginated.

        The unified diff is a navigation aid only. Callers that need content
        must fetch the whole file at an explicit revision.
        """
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._get(
                f"/repos/{owner}/{repo}/pulls/{number}/files",
                params={"per_page": 100, "page": page},
            )
            if not batch:
                break
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    def get_file_at_ref(self, owner: str, repo: str, path: str, ref: str) -> bytes | None:
        """
        Fetch the complete contents of one file at one exact revision.

        Returns None when the file does not exist at that revision, which is a
        normal outcome for an added or deleted file.
        """
        response = self._session.get(
            f"{API_ROOT}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw"},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise GitHubError(
                f"GET contents {path}@{ref[:8]} failed: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )
        return response.content

    # ----------------------------------------------------------------- private

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._session.get(
            f"{API_ROOT}{path}", params=params, timeout=TIMEOUT_SECONDS
        )
        if not response.ok:
            raise GitHubError(
                f"GET {path} failed: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()
