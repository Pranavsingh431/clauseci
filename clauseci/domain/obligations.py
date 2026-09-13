"""
Typed retention obligations.

Two layers on purpose.

`ModelCandidate` is what the model is allowed to say. It carries language only:
a number, a unit, the categories a clause covers, and the quote that supports
it. It carries no identity, because the model must not be able to claim which
customer or which source a finding belongs to.

`CandidateRetentionObligation` is what deterministic code produces after
stamping identity from the snapshot and the manifest, and after verifying the
quote against the source text.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ObligationType(str, Enum):
    RETENTION_UPPER_BOUND = "RETENTION_UPPER_BOUND"


class Operator(str, Enum):
    AT_MOST = "<="


class Unit(str, Enum):
    DAYS = "days"


class Category(str, Enum):
    APPLICATION_LOGS = "application_logs"
    DIAGNOSTIC_LOGS = "diagnostic_logs"
    AUDIT_LOGS = "audit_logs"


ALLOWED_CATEGORIES: tuple[str, ...] = tuple(c.value for c in Category)


class SemanticStatus(str, Enum):
    #: A supported obligation was found and quoted.
    EXTRACTED = "EXTRACTED"
    #: The document says nothing that fits the supported schema.
    NO_SUPPORTED_OBLIGATION = "NO_SUPPORTED_OBLIGATION"
    #: Relevant language exists but cannot be represented without guessing.
    AMBIGUOUS = "AMBIGUOUS"
    #: Something prevented a reliable reading.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NO_REPRESENTED_OBLIGATION = "NO_REPRESENTED_OBLIGATION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelCandidate(Frozen):
    """One retention clause as the model read it. Language only, no identity."""

    operator: str
    value: int
    unit: str
    covered_categories: tuple[str, ...]
    page: int | None = None
    section: str | None = None
    quote: str
    document_declared_execution_status: str | None = None
    document_declared_effective_date: str | None = None
    amendment_reference: str | None = None
    supersession_reference: str | None = None
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()


class ModelDocumentExtraction(Frozen):
    """The model's complete reading of one document."""

    semantic_status: SemanticStatus
    review_reason: str | None = None
    candidates: tuple[ModelCandidate, ...] = ()


class CandidateRetentionObligation(Frozen):
    """A validated candidate, bound to a customer and a source."""

    # Identity, stamped by deterministic code. Never supplied by the model.
    customer_id: str
    source_id: str
    source_file_id: str
    source_content_digest: str

    obligation_type: ObligationType = ObligationType.RETENTION_UPPER_BOUND
    operator: Operator = Operator.AT_MOST
    value: int
    unit: Unit = Unit.DAYS
    covered_categories: tuple[Category, ...]

    page: int | None = None
    section: str | None = None
    quote: str

    document_declared_execution_status: str | None = None
    document_declared_effective_date: str | None = None
    amendment_reference: str | None = None
    supersession_reference: str | None = None
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()

    semantic_status: SemanticStatus = SemanticStatus.EXTRACTED
    review_reason: str | None = None

    #: Proves the quote occurs in the source text. Does not prove the reading
    #: is legally correct.
    quote_verified: bool
    #: Derived deterministically from manifest provenance, never from the model.
    eligible_to_control: bool
    ineligibility_reasons: tuple[str, ...] = ()


class RejectedSource(Frozen):
    """A competing source that did not control, and why."""

    source_id: str
    reason: str


class ResolvedRetentionObligation(Frozen):
    """The controlling obligation for one customer and one category."""

    customer_id: str
    category: Category
    obligation_type: ObligationType = ObligationType.RETENTION_UPPER_BOUND
    operator: Operator | None = None
    value: int | None = None
    unit: Unit | None = None
    #: Every category the controlling clause covers, which may be wider than
    #: the single category this record resolves.
    covered_categories: tuple[Category, ...] = ()

    controlling_source_id: str | None = None
    controlling_source_file_id: str | None = None
    source_digest: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None

    rejected_sources: tuple[RejectedSource, ...] = ()
    conditions: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()

    resolution_status: ResolutionStatus
    review_reason: str | None = None


class SemanticRunMetadata(Frozen):
    """What was used to produce a result, so a run can be reproduced."""

    model: str
    prompt_version: str
    schema_version: str
    obligation_family: str
    requests: int = 0
    repairs: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    latency_seconds_total: float = 0.0
    latency_seconds_max: float = 0.0


class ObligationAnalysis(Frozen):
    """Everything Phase 3 produces. No verdict, no external action."""

    snapshot_schema_version: str
    policy_version: str
    corpus_digest: str
    customer_cohort: tuple[str, ...]
    candidates: tuple[CandidateRetentionObligation, ...]
    resolved: tuple[ResolvedRetentionObligation, ...]
    metadata: SemanticRunMetadata
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    def for_customer(self, customer_id: str) -> list[ResolvedRetentionObligation]:
        return [r for r in self.resolved if r.customer_id == customer_id]

    def resolved_value(self, customer_id: str, category: str) -> int | None:
        for record in self.resolved:
            if record.customer_id == customer_id and record.category.value == category:
                return record.value
        return None

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=True)
