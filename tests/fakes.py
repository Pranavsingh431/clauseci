"""
Offline stand ins for the provider adapters.

These serve bytes from the repository's own fixtures, so snapshot building can
be tested deterministically without touching GitHub or Drive. The PDFs are the
real ones, so text extraction is genuine rather than stubbed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clauseci.adapters.drive import DriveFile
from clauseci.adapters.github import PullRequestRef, RepositoryNotAllowed

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CONTRACTS = ROOT / "demo_contracts"

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


class FakeGitHubReader:
    """Serves one pull request from local fixture files. Reads only."""

    def __init__(
        self,
        changed_files: list[dict[str, Any]] | None = None,
        base_config: str | None = None,
        head_config: str | None = None,
        allowed: str = "Pranavsingh431/clauseci-demo-saas",
    ) -> None:
        self.allowed = allowed
        self.changed_files = changed_files if changed_files is not None else [
            {"filename": "config/retention.yaml", "status": "modified",
             "additions": 2, "deletions": 2},
        ]
        self.base_config = (
            base_config
            if base_config is not None
            else (FIXTURES / "retention_baseline.yaml").read_text()
        )
        self.head_config = (
            head_config
            if head_config is not None
            else (FIXTURES / "retention_requested.yaml").read_text()
        )
        #: every (path, ref) pair that was asked for, so tests can assert the
        #: reader fetched whole files at explicit revisions
        self.fetched: list[tuple[str, str]] = []

    def resolve_reference(self, reference: str) -> PullRequestRef:
        owner, repo = self.allowed.split("/")
        if "/" in reference and "github.com" in reference:
            parts = reference.split("github.com/")[1].split("/")
            if f"{parts[0]}/{parts[1]}".lower() != self.allowed.lower():
                raise RepositoryNotAllowed(f"{parts[0]}/{parts[1]} is not allowlisted")
        return PullRequestRef(owner=owner, repo=repo, number=1)

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        return {"id": 1367664133, "full_name": f"{owner}/{repo}"}

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        return {
            "number": number,
            "title": "Raise application and diagnostic log retention to 90 days",
            "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
            "base": {"sha": BASE_SHA, "ref": "main"},
            "head": {"sha": HEAD_SHA, "ref": "feature/increase-log-retention"},
        }

    def list_changed_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        return list(self.changed_files)

    def get_file_at_ref(self, owner: str, repo: str, path: str, ref: str) -> bytes | None:
        self.fetched.append((path, ref))
        if path != "config/retention.yaml":
            return None
        if ref == BASE_SHA:
            return self.base_config.encode("utf-8")
        if ref == HEAD_SHA:
            return self.head_config.encode("utf-8")
        return None


class FakeDriveReader:
    """Serves the repository's own contract PDFs as if they were Drive files."""

    def __init__(self, names: list[str] | None = None, folder_id: str = "folder-test-id") -> None:
        self._folder_id = folder_id
        self.names = names if names is not None else sorted(
            p.name for p in CONTRACTS.glob("*.pdf")
        )
        #: lets a test mutate one file's bytes without touching the real fixture
        self.overrides: dict[str, bytes] = {}
        self.list_order_reversed = False

    @property
    def folder_id(self) -> str:
        return self._folder_id

    def _file_id(self, name: str) -> str:
        return f"drive-{name.split('_')[0]}"

    def list_files(self) -> list[dict[str, Any]]:
        names = list(reversed(self.names)) if self.list_order_reversed else list(self.names)
        return [
            {
                "id": self._file_id(name),
                "name": name,
                "mimeType": "application/pdf",
                "size": str(len(self._bytes(name))),
                "modifiedTime": "2026-09-13T00:00:00.000Z",
                "version": "3",
                "headRevisionId": f"rev-{name[:2]}",
            }
            for name in names
        ]

    def _bytes(self, name: str) -> bytes:
        if name in self.overrides:
            return self.overrides[name]
        return (CONTRACTS / name).read_bytes()

    def download(self, metadata: dict[str, Any]) -> DriveFile:
        name = metadata["name"]
        content = self._bytes(name)
        return DriveFile(
            file_id=metadata["id"],
            name=name,
            mime_type=metadata["mimeType"],
            size_bytes=len(content),
            modified_time=metadata.get("modifiedTime"),
            version=metadata.get("version"),
            head_revision_id=metadata.get("headRevisionId"),
            md5_checksum=None,
            content=content,
        )
