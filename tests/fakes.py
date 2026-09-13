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


class StubSemanticClient:
    """
    A semantic client that returns canned replies. No network.

    Lets the deterministic half of the analyzer be tested on its own, including
    cases the real model happens to get right for its own reasons. If the gate
    only works because the model behaved, it is not a gate.
    """

    def __init__(
        self,
        extractions: dict[str, dict] | None = None,
        resolution: dict | None = None,
        model: str = "stub-model",
    ) -> None:
        self.model = model
        self.extractions = extractions or {}
        self.resolution = resolution
        self.calls: list[tuple[str, str]] = []

    def structured(self, *, system, user, schema, schema_name, usage, max_tokens=3000):
        if schema_name == "retention_resolution":
            self.calls.append(("resolve", user))
            if self.resolution is None:
                raise AssertionError("a resolution was requested but none was stubbed")
            return self.resolution

        source_id = ""
        for line in user.splitlines():
            if line.strip().startswith("source_id"):
                source_id = line.split()[-1]
                break
        self.calls.append(("extract", source_id))
        return self.extractions.get(
            source_id,
            {"semantic_status": "NO_SUPPORTED_OBLIGATION", "review_reason": None,
             "candidates": []},
        )


def candidate_payload(
    value: int,
    categories: list[str],
    quote: str,
    *,
    section: str | None = "2.1",
    execution: str | None = "EXECUTED",
    effective: str | None = "2026-08-20",
    supersession: str | None = None,
    operator: str = "<=",
    unit: str = "days",
) -> dict:
    return {
        "operator": operator,
        "value": value,
        "unit": unit,
        "covered_categories": categories,
        "page": 1,
        "section": section,
        "quote": quote,
        "document_declared_execution_status": execution,
        "document_declared_effective_date": effective,
        "amendment_reference": None,
        "supersession_reference": supersession,
        "conditions": [],
        "exceptions": [],
    }


def extraction_payload(candidates: list[dict], status: str = "EXTRACTED") -> dict:
    return {"semantic_status": status, "review_reason": None, "candidates": candidates}


def local_snapshot():
    """A snapshot whose evidence corpus is the repository's own contract PDFs."""
    from clauseci.domain.snapshot import build_analysis_snapshot
    from clauseci.registry import load_registry

    return build_analysis_snapshot(
        "1", None, load_registry(), FakeGitHubReader(), FakeDriveReader()
    )


def local_text_provider():
    from clauseci.domain.evidence_text import EvidenceTextProvider, LocalBytesSource

    return EvidenceTextProvider(LocalBytesSource(CONTRACTS), cache_dir=None, use_cache=False)


class FakeStatusWriter:
    """In memory GitHub commit statuses. Records every write."""

    def __init__(self, allowed: str = "Pranavsingh431/clauseci-demo-saas") -> None:
        self.allowed = allowed
        self.writes: list[dict] = []
        #: (sha, context) -> most recent status
        self.statuses: dict[tuple[str, str], dict] = {}
        #: lets a test simulate provider state diverging from what was written
        self.tamper = None

    def set_commit_status(self, owner, repo, sha, *, state, context, description,
                          target_url=None):
        if f"{owner}/{repo}" != self.allowed:
            raise RuntimeError(f"{owner}/{repo} is not allowlisted")
        if len(sha) != 40:
            raise RuntimeError(f"refusing a partial sha {sha!r}")
        record = {"id": 1000 + len(self.writes), "state": state, "context": context,
                  "description": description, "sha": sha}
        self.writes.append(dict(record))
        stored = dict(record)
        if self.tamper:
            stored = self.tamper(stored)
        self.statuses[(sha, stored["context"])] = stored
        return record

    def read_commit_statuses(self, owner, repo, sha):
        return [s for (stored_sha, _), s in self.statuses.items() if stored_sha == sha]

    def latest_status_for_context(self, owner, repo, sha, context):
        return self.statuses.get((sha, context))


class FakeSlackWriter:
    """In memory Slack channel holding root messages only."""

    def __init__(self, channel_id: str = "C-TEST") -> None:
        self._channel_id = channel_id
        self.messages: dict[str, str] = {}
        self.posts = 0
        self.updates = 0
        self.tamper = None

    @property
    def channel_id(self) -> str:
        return self._channel_id

    def find_case_by_marker(self, marker, limit=200):
        from clauseci.adapters.slack_write import SlackMessageRef
        return [SlackMessageRef(self._channel_id, ts)
                for ts, text in sorted(self.messages.items()) if marker in text]

    def post_case(self, text):
        from clauseci.adapters.slack_write import SlackMessageRef
        self.posts += 1
        ts = f"170000000.{self.posts:06d}"
        self.messages[ts] = self.tamper(text) if self.tamper else text
        return SlackMessageRef(self._channel_id, ts)

    def update_case(self, ref, text):
        self.updates += 1
        self.messages[ref.ts] = self.tamper(text) if self.tamper else text
        return ref

    def read_case(self, ref):
        if ref.ts not in self.messages:
            raise RuntimeError("not found")
        return {"ts": ref.ts, "text": self.messages[ref.ts]}

    def seed_case(self, text: str) -> str:
        ts = f"169000000.{len(self.messages) + 1:06d}"
        self.messages[ts] = text
        return ts
