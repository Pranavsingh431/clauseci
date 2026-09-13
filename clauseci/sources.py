"""
The trusted source manifest, and deterministic eligibility.

The manifest records provenance facts about a document. Whether a document may
establish a current obligation is derived here in code, never stored as data.
That keeps a judgement out of the fixture file.

An ineligible source is not discarded. It stays in the corpus as evidence and
its candidate obligations are still extracted. It simply cannot control.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

import yaml

from clauseci.registry import CustomerRegistry

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parent / "data" / "source_manifest.yaml"

EXECUTED = "executed"


class ManifestError(ValueError):
    """The source manifest is not usable."""


class IneligibilityReason(str, Enum):
    WRONG_LEGAL_ENTITY = "wrong_legal_entity"
    NOT_EXECUTED = "not_executed"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    OUTSIDE_CUSTOMER_CORPUS = "outside_customer_corpus"
    NOT_IN_MANIFEST = "not_in_manifest"


@dataclass(frozen=True)
class TrustedSource:
    source_id: str
    document_name: str
    customer_id: str
    legal_entity: str
    document_type: str
    execution_status: str
    effective_date: date | None
    amends: tuple[str, ...]
    provenance_note: str


@dataclass(frozen=True)
class SourceEligibility:
    """Why a source may or may not establish a current obligation."""

    source_id: str
    eligible_to_control: bool
    reasons: tuple[IneligibilityReason, ...]

    def explain(self) -> str:
        if self.eligible_to_control:
            return "executed, effective, and associated with this customer"
        return ", ".join(reason.value for reason in self.reasons)


@dataclass(frozen=True)
class SourceManifest:
    manifest_version: int
    sources: tuple[TrustedSource, ...]

    def by_document_name(self, name: str) -> TrustedSource | None:
        for source in self.sources:
            if source.document_name == name:
                return source
        return None

    def by_source_id(self, source_id: str) -> TrustedSource | None:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None


def load_source_manifest(path: Path | None = None) -> SourceManifest:
    raw = yaml.safe_load(Path(path or DEFAULT_MANIFEST_PATH).read_text())
    if not isinstance(raw, dict) or "sources" not in raw:
        raise ManifestError("manifest must be a mapping containing 'sources'")

    sources: list[TrustedSource] = []
    for entry in raw["sources"]:
        try:
            raw_date = entry.get("effective_date")
            sources.append(
                TrustedSource(
                    source_id=str(entry["source_id"]),
                    document_name=str(entry["document_name"]),
                    customer_id=str(entry["customer_id"]),
                    legal_entity=str(entry["legal_entity"]),
                    document_type=str(entry["document_type"]),
                    execution_status=str(entry["execution_status"]),
                    effective_date=date.fromisoformat(raw_date) if raw_date else None,
                    amends=tuple(entry.get("amends", [])),
                    provenance_note=str(entry.get("provenance_note", "")),
                )
            )
        except KeyError as exc:
            raise ManifestError(f"manifest entry is missing {exc}") from exc

    ids = [s.source_id for s in sources]
    if len(ids) != len(set(ids)):
        raise ManifestError("manifest contains duplicate source_id values")

    return SourceManifest(manifest_version=int(raw.get("manifest_version", 0)),
                          sources=tuple(sources))


def assess_eligibility(
    source: TrustedSource | None,
    customer_id: str,
    registry: CustomerRegistry,
    as_of: date,
) -> SourceEligibility:
    """
    Decide whether a source may establish a current obligation for a customer.

    Deterministic. No model is consulted. Every failing condition is recorded,
    not just the first one, so the explanation is complete.
    """
    if source is None:
        return SourceEligibility(
            source_id="", eligible_to_control=False,
            reasons=(IneligibilityReason.NOT_IN_MANIFEST,),
        )

    reasons: list[IneligibilityReason] = []

    if source.customer_id != customer_id:
        reasons.append(IneligibilityReason.WRONG_LEGAL_ENTITY)

    try:
        registered = registry.by_id(customer_id)
        if source.document_name not in registered.eligible_documents:
            reasons.append(IneligibilityReason.OUTSIDE_CUSTOMER_CORPUS)
    except Exception:  # noqa: BLE001 - unknown customer is handled by the caller
        reasons.append(IneligibilityReason.OUTSIDE_CUSTOMER_CORPUS)

    if source.execution_status != EXECUTED:
        reasons.append(IneligibilityReason.NOT_EXECUTED)

    if source.effective_date is not None and source.effective_date > as_of:
        reasons.append(IneligibilityReason.NOT_YET_EFFECTIVE)

    return SourceEligibility(
        source_id=source.source_id,
        eligible_to_control=not reasons,
        reasons=tuple(reasons),
    )
