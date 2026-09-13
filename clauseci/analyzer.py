"""
The bounded semantic obligation analyzer.

Produces validated, source bound retention obligations for the customer cohort
in a snapshot. It does not decide whether the pull request passes. That is a
later phase.

No provider is written to. The only outbound call is to OpenRouter.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clauseci.adapters.openrouter import SemanticClient, Usage
from clauseci.domain.authority import resolve_category
from clauseci.domain.digests import sha256_text
from clauseci.domain.evidence_text import DigestMismatch, EvidenceTextProvider
from clauseci.domain.extraction import ValidationFailure, validate_candidate
from clauseci.domain.models import AnalysisSnapshot, DriveSourceSnapshot
from clauseci.domain.obligations import (
    CandidateRetentionObligation,
    Category,
    ModelDocumentExtraction,
    ObligationAnalysis,
    ResolutionStatus,
    ResolvedRetentionObligation,
    SemanticRunMetadata,
)
from clauseci.domain.prompts import (
    EXTRACTION_SCHEMA,
    EXTRACTION_SYSTEM,
    RESOLUTION_SCHEMA,
    RESOLUTION_SYSTEM,
    extraction_user_message,
    resolution_user_message,
)
from clauseci.domain.semantic_cache import SemanticCache
from clauseci.registry import CustomerRegistry, load_registry
from clauseci.settings import ROOT
from clauseci.sources import SourceManifest, assess_eligibility, load_source_manifest
from clauseci.versions import (
    POLICY_VERSION,
    SEMANTIC_MODEL,
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_SCHEMA_VERSION,
    SUPPORTED_OBLIGATION_FAMILY,
)

RUN_RECORD_DIR = ROOT / "runs" / "semantic_runs"


def analyze_retention_obligations(
    snapshot: AnalysisSnapshot,
    *,
    text_provider: EvidenceTextProvider,
    client: SemanticClient | None = None,
    registry: CustomerRegistry | None = None,
    manifest: SourceManifest | None = None,
    cache: SemanticCache | None = None,
    as_of: date | None = None,
    run_id: str | None = None,
    record_dir: Path | None = RUN_RECORD_DIR,
) -> ObligationAnalysis:
    """Read the evidence corpus and return validated retention obligations."""
    registry = registry or load_registry()
    manifest = manifest or load_source_manifest()
    cache = cache if cache is not None else SemanticCache()
    client = client or SemanticClient()
    as_of = as_of or snapshot.created_at.date()

    usage = Usage()
    warnings: list[str] = []
    candidates: list[CandidateRetentionObligation] = []
    records: list[dict[str, Any]] = []

    for source in snapshot.evidence_corpus.sources:
        trusted = manifest.by_document_name(source.name)
        if trusted is None:
            warnings.append(
                f"{source.name} is in the corpus but not in the trusted source "
                f"manifest, so it cannot be used as evidence"
            )
            continue

        try:
            document_text = text_provider.text_for(source)
        except DigestMismatch as exc:
            warnings.append(f"{source.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{source.name}: text could not be read: {exc}")
            continue

        for customer_id in source.customer_ids:
            extraction, record = _extract_document(
                client=client,
                cache=cache,
                usage=usage,
                customer_id=customer_id,
                legal_entity=trusted.legal_entity,
                source_id=trusted.source_id,
                source=source,
                document_text=document_text,
            )
            records.append(record)

            if extraction is None:
                warnings.append(
                    f"{source.name} for {customer_id}: the model reply could not be "
                    f"validated, so nothing from this document is used"
                )
                continue

            if extraction.semantic_status.value in {"AMBIGUOUS", "REVIEW_REQUIRED"}:
                warnings.append(
                    f"{source.name} for {customer_id}: "
                    f"{extraction.semantic_status.value}. "
                    f"{extraction.review_reason or 'no reason given'}"
                )

            eligibility = assess_eligibility(trusted, customer_id, registry, as_of)
            for model_candidate in extraction.candidates:
                result = validate_candidate(
                    model_candidate,
                    customer_id=customer_id,
                    source=trusted,
                    snapshot_source=source,
                    eligibility=eligibility,
                    document_text=document_text,
                    registry=registry,
                    expected_file_id=source.file_id,
                    expected_digest=source.content_sha256,
                )
                if isinstance(result, ValidationFailure):
                    warnings.append(
                        f"refused a candidate from {result.source_id} for "
                        f"{customer_id}: {result.reason}"
                    )
                else:
                    candidates.append(result)

    resolution_step = _make_resolution_step(client, cache, usage, manifest, warnings)

    resolved: list[ResolvedRetentionObligation] = []
    for customer_id in snapshot.customer_cohort:
        mine = [c for c in candidates if c.customer_id == customer_id]
        for category in Category:
            resolved.append(resolve_category(customer_id, category, mine, resolution_step))

    metadata = SemanticRunMetadata(
        model=client.model,
        prompt_version=SEMANTIC_PROMPT_VERSION,
        schema_version=SEMANTIC_SCHEMA_VERSION,
        obligation_family=SUPPORTED_OBLIGATION_FAMILY,
        requests=usage.requests,
        repairs=usage.repairs,
        cache_hits=cache.hits,
        cache_misses=cache.misses,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=round(usage.cost_usd, 6) if usage.cost_reported else None,
        latency_seconds_total=round(usage.latency_total, 3),
        latency_seconds_max=round(usage.latency_max, 3),
    )

    analysis = ObligationAnalysis(
        snapshot_schema_version=snapshot.snapshot_schema_version,
        policy_version=POLICY_VERSION,
        corpus_digest=snapshot.corpus_digest,
        customer_cohort=snapshot.customer_cohort,
        candidates=tuple(candidates),
        resolved=tuple(resolved),
        metadata=metadata,
        warnings=tuple(warnings),
    )

    if record_dir is not None:
        _write_run_record(record_dir, run_id, snapshot, analysis, records)

    return analysis


def _extract_document(
    *,
    client: SemanticClient,
    cache: SemanticCache,
    usage: Usage,
    customer_id: str,
    legal_entity: str,
    source_id: str,
    source: DriveSourceSnapshot,
    document_text: str,
) -> tuple[ModelDocumentExtraction | None, dict[str, Any]]:
    """One document, one customer, one bounded semantic reading."""
    key = SemanticCache.build_key(
        step="extract",
        model=client.model,
        prompt_version=SEMANTIC_PROMPT_VERSION,
        schema_version=SEMANTIC_SCHEMA_VERSION,
        content_digest=source.content_sha256,
        customer_id=customer_id,
        obligation_family=SUPPORTED_OBLIGATION_FAMILY,
    )
    user = extraction_user_message(
        customer_id=customer_id,
        legal_entity=legal_entity,
        source_id=source_id,
        file_id=source.file_id,
        content_digest=source.content_sha256,
        document_text=document_text,
    )
    record: dict[str, Any] = {
        "step": "extract",
        "customer_id": customer_id,
        "source_id": source_id,
        "source_file_id": source.file_id,
        "source_content_digest": source.content_sha256,
        "input_digest": sha256_text(user),
        "model": client.model,
        "prompt_version": SEMANTIC_PROMPT_VERSION,
        "schema_version": SEMANTIC_SCHEMA_VERSION,
    }

    payload = cache.get(key)
    record["cache"] = "hit" if payload is not None else "miss"
    if payload is None:
        payload = client.structured(
            system=EXTRACTION_SYSTEM,
            user=user,
            schema=EXTRACTION_SCHEMA,
            schema_name="retention_extraction",
            usage=usage,
        )
        cache.put(key, payload)

    record["raw_result"] = payload
    try:
        extraction = ModelDocumentExtraction.model_validate(payload)
    except ValidationError as exc:
        record["validation"] = f"rejected: {exc.error_count()} schema error(s)"
        return None, record

    record["validation"] = "accepted"
    record["semantic_status"] = extraction.semantic_status.value
    record["candidate_count"] = len(extraction.candidates)
    return extraction, record


def _make_resolution_step(
    client: SemanticClient,
    cache: SemanticCache,
    usage: Usage,
    manifest: SourceManifest,
    warnings: list[str],
):
    """Build the bounded step used only when eligible clauses actually compete."""

    def step(customer_id: str, category: str, competing: list[CandidateRetentionObligation]):
        clauses = []
        for candidate in competing:
            trusted = manifest.by_source_id(candidate.source_id)
            clauses.append(
                {
                    "source_id": candidate.source_id,
                    "document_type": trusted.document_type if trusted else "unknown",
                    "document_declared_effective_date": candidate.document_declared_effective_date,
                    "document_declared_execution_status": candidate.document_declared_execution_status,
                    "amendment_reference": candidate.amendment_reference,
                    "supersession_reference": candidate.supersession_reference,
                    "section": candidate.section,
                    "value": candidate.value,
                    "covered_categories": [c.value for c in candidate.covered_categories],
                    "quote": candidate.quote,
                }
            )

        user = resolution_user_message(
            customer_id=customer_id, category=category, clauses=clauses
        )
        key = SemanticCache.build_key(
            step="resolve",
            model=client.model,
            prompt_version=SEMANTIC_PROMPT_VERSION,
            schema_version=SEMANTIC_SCHEMA_VERSION,
            content_digest=sha256_text(
                "|".join(f"{c['source_id']}:{c['value']}:{c['quote']}" for c in clauses)
            ),
            customer_id=customer_id,
            obligation_family=SUPPORTED_OBLIGATION_FAMILY,
            extra=category,
        )

        payload = cache.get(key)
        if payload is None:
            try:
                payload = client.structured(
                    system=RESOLUTION_SYSTEM,
                    user=user,
                    schema=RESOLUTION_SCHEMA,
                    schema_name="retention_resolution",
                    usage=usage,
                    max_tokens=1500,
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(
                    f"resolution failed for {customer_id} {category}: {exc}"
                )
                return None, ResolutionStatus.REVIEW_REQUIRED.value, str(exc), {}
            cache.put(key, payload)

        rejections = {
            int(item["index"]): str(item["reason"])
            for item in payload.get("rejections", [])
            if isinstance(item, dict) and "index" in item
        }
        return (
            payload.get("selected_index"),
            str(payload.get("resolution_status", ResolutionStatus.REVIEW_REQUIRED.value)),
            str(payload.get("reason", "")),
            rejections,
        )

    return step


def _write_run_record(
    directory: Path,
    run_id: str | None,
    snapshot: AnalysisSnapshot,
    analysis: ObligationAnalysis,
    records: list[dict[str, Any]],
) -> Path:
    """Save machine readable evidence of the run. Never contains a credential."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    identifier = run_id or f"pr{snapshot.pr_number}"
    path = directory / f"semantic-{identifier}-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "run_id": identifier,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "corpus_digest": snapshot.corpus_digest,
                "policy_version": analysis.policy_version,
                "metadata": analysis.metadata.model_dump(mode="json"),
                "steps": records,
                "resolved": [r.model_dump(mode="json") for r in analysis.resolved],
                "warnings": list(analysis.warnings),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path
