"""
Typed snapshot models.

An AnalysisSnapshot is immutable evidence. It records what was read, from
where, and at which exact revision. It deliberately records no contractual cap,
no verdict and no expected result. Those belong to later phases and to the
evaluation oracle respectively.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from clauseci.domain.classification import SurfaceClass


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GitHubFileSnapshot(Frozen):
    """One changed file, classified, with a digest of its bytes at each end."""

    path: str
    status: str
    additions: int = 0
    deletions: int = 0
    previous_path: str | None = None
    surface: SurfaceClass
    #: Digest of the complete file at the base revision. None if absent there.
    base_content_sha256: str | None = None
    #: Digest of the complete file at the head revision. None if deleted.
    head_content_sha256: str | None = None


class GitHubPRSnapshot(Frozen):
    """Identity of the pull request, pinned to two exact revisions."""

    owner: str
    repo: str
    repository_id: int | None = None
    pr_number: int
    pr_title: str
    pr_url: str
    target_branch: str
    base_sha: str
    head_sha: str
    changed_files: tuple[GitHubFileSnapshot, ...]

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


class ResolvedValue(Frozen):
    """One effective configuration value, with where it came from."""

    customer_id: str
    category: str
    effective_value: int | None
    unit: str
    source_type: str
    config_path: str | None = None
    #: Set when the category has no value anywhere in the configuration.
    unresolved_reason: str | None = None


class CustomerConfigSnapshot(Frozen):
    """Every judged category for one customer at one revision."""

    customer_id: str
    legal_entity_name: str
    config_key: str
    present_in_config: bool
    values: tuple[ResolvedValue, ...]

    def value_for(self, category: str) -> ResolvedValue | None:
        for value in self.values:
            if value.category == category:
                return value
        return None


class DriveSourceSnapshot(Frozen):
    """One contract document as it existed when it was read."""

    file_id: str
    name: str
    mime_type: str
    size_bytes: int | None = None
    modified_time: str | None = None
    #: Provider revision identity. Drive exposes `version` and `headRevisionId`.
    version: str | None = None
    head_revision_id: str | None = None
    #: Digest of the downloaded bytes, computed locally rather than trusted.
    content_sha256: str
    #: Digest of the text extracted from those bytes.
    text_sha256: str | None = None
    text_chars: int = 0
    extraction_error: str | None = None
    #: Which registry customers this document belongs to. Ownership only.
    customer_ids: tuple[str, ...] = ()


class EvidenceCorpusSnapshot(Frozen):
    """The complete set of documents read, and a digest over all of them."""

    folder_id: str
    source_count: int
    sources: tuple[DriveSourceSnapshot, ...]
    corpus_digest: str
    #: Documents the registry expects but which were not found in the folder.
    missing_documents: tuple[str, ...] = ()
    #: Documents present in the folder that no registry customer claims.
    unclaimed_documents: tuple[str, ...] = ()


class AnalysisSnapshot(Frozen):
    """Immutable evidence for one pull request at one pair of revisions."""

    snapshot_schema_version: str
    policy_version: str
    parser_version: str
    created_at: datetime

    repository: str
    repository_id: int | None = None
    pr_number: int
    pr_title: str
    pr_url: str
    target_branch: str
    base_sha: str
    head_sha: str

    changed_files: tuple[GitHubFileSnapshot, ...]
    supported_changed_surfaces: tuple[str, ...]
    unknown_retention_changed_surfaces: tuple[str, ...]
    unsupported_changed_surfaces: tuple[str, ...]

    customer_cohort: tuple[str, ...]
    base_effective_config: tuple[CustomerConfigSnapshot, ...]
    head_effective_config: tuple[CustomerConfigSnapshot, ...]

    evidence_corpus: EvidenceCorpusSnapshot

    #: Anything that stopped a part of the snapshot being built. Never silently
    #: dropped, because a later phase must turn these into review required.
    collection_warnings: tuple[str, ...] = Field(default_factory=tuple)

    @property
    def corpus_digest(self) -> str:
        return self.evidence_corpus.corpus_digest

    @property
    def has_supported_change(self) -> bool:
        return bool(self.supported_changed_surfaces)

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "AnalysisSnapshot":
        import json

        return cls.model_validate(json.loads(text))

    def summary_rows(self) -> list[dict[str, Any]]:
        """Base and head effective values side by side, for display."""
        rows: list[dict[str, Any]] = []
        head_by_customer = {c.customer_id: c for c in self.head_effective_config}
        for base in self.base_effective_config:
            head = head_by_customer.get(base.customer_id)
            for value in base.values:
                head_value = head.value_for(value.category) if head else None
                rows.append(
                    {
                        "customer_id": base.customer_id,
                        "category": value.category,
                        "base": value.effective_value,
                        "head": head_value.effective_value if head_value else None,
                        "unit": value.unit,
                        "base_source": value.source_type,
                        "head_source": head_value.source_type if head_value else None,
                    }
                )
        return rows
