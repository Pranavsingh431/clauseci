"""
Building an immutable analysis snapshot.

This is the deterministic evidence foundation. No model is called here, and
nothing outside this process is mutated. Every later decision will be bound to
one of these snapshots.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from clauseci.adapters.drive import DriveReader, extract_pdf_text
from clauseci.adapters.github import GitHubReader
from clauseci.config_resolution import (
    CATEGORY_TO_FIELD,
    ConfigError,
    RETENTION_UNIT,
    RetentionConfig,
    SUPPORTED_CATEGORIES,
    UnknownCustomerError,
    parse_retention_config,
    resolve_effective,
)
from clauseci.domain.classification import SurfaceClass, classify_path
from clauseci.domain.digests import corpus_digest, sha256_bytes, sha256_text
from clauseci.domain.models import (
    AnalysisSnapshot,
    CustomerConfigSnapshot,
    DriveSourceSnapshot,
    EvidenceCorpusSnapshot,
    GitHubFileSnapshot,
    ResolvedValue,
)
from clauseci.registry import CustomerRegistry
from clauseci.settings import SUPPORTED_RETENTION_PATHS, Settings
from clauseci.versions import (
    CONFIG_PARSER_VERSION,
    POLICY_VERSION,
    SNAPSHOT_SCHEMA_VERSION,
)

# Surfaces whose full bytes are worth fetching. Unsupported files are recorded
# in the inventory but their contents are not downloaded.
FETCH_CONTENT_FOR = (
    SurfaceClass.SUPPORTED_RETENTION,
    SurfaceClass.UNKNOWN_RETENTION_RELATED,
)


def resolve_cohort(
    config: RetentionConfig | None, registry: CustomerRegistry
) -> tuple[CustomerConfigSnapshot, ...]:
    """
    Resolve every judged category for every registry customer.

    A customer the registry knows about but the configuration does not mention
    is recorded as absent rather than skipped. A category with no value
    anywhere is recorded with a reason rather than defaulted to something.
    """
    snapshots: list[CustomerConfigSnapshot] = []

    for customer in registry.customers:
        present = config is not None and customer.config_key in config.customers
        values: list[ResolvedValue] = []

        for category in SUPPORTED_CATEGORIES:
            field = CATEGORY_TO_FIELD[category]
            if config is None:
                values.append(
                    ResolvedValue(
                        customer_id=customer.customer_id,
                        category=category,
                        effective_value=None,
                        unit=RETENTION_UNIT,
                        source_type="unresolved",
                        unresolved_reason="retention configuration was not readable",
                    )
                )
                continue
            try:
                resolved = resolve_effective(config, customer.config_key, field)
            except UnknownCustomerError:
                values.append(
                    ResolvedValue(
                        customer_id=customer.customer_id,
                        category=category,
                        effective_value=None,
                        unit=RETENTION_UNIT,
                        source_type="unresolved",
                        unresolved_reason=(
                            f"config key {customer.config_key!r} is absent from the "
                            f"retention configuration"
                        ),
                    )
                )
            except ConfigError as exc:
                values.append(
                    ResolvedValue(
                        customer_id=customer.customer_id,
                        category=category,
                        effective_value=None,
                        unit=RETENTION_UNIT,
                        source_type="unresolved",
                        unresolved_reason=str(exc),
                    )
                )
            else:
                values.append(
                    ResolvedValue(
                        customer_id=customer.customer_id,
                        category=category,
                        effective_value=resolved.value,
                        unit=resolved.unit,
                        source_type=resolved.source.value,
                        config_path=resolved.config_path or None,
                    )
                )

        snapshots.append(
            CustomerConfigSnapshot(
                customer_id=customer.customer_id,
                legal_entity_name=customer.legal_entity_name,
                config_key=customer.config_key,
                present_in_config=present,
                values=tuple(values),
            )
        )

    return tuple(snapshots)


def build_evidence_corpus(
    drive: DriveReader, registry: CustomerRegistry, warnings: list[str]
) -> EvidenceCorpusSnapshot:
    """
    Read every registry associated document and digest it.

    Association comes from the registry, never from the filename. Superseded,
    unsigned and adversarial documents are all included. Deciding which one
    controls is not this phase's job.
    """
    listed = drive.list_files()
    expected = set(registry.all_eligible_documents())
    seen_names: set[str] = set()
    sources: list[DriveSourceSnapshot] = []
    unclaimed: list[str] = []

    for metadata in listed:
        name = metadata["name"]
        seen_names.add(name)
        customer_ids = registry.customers_for_document(name)
        if not customer_ids:
            unclaimed.append(name)
            continue

        downloaded = drive.download(metadata)
        content_hash = sha256_bytes(downloaded.content)

        text_hash: str | None = None
        text_chars = 0
        extraction_error: str | None = None
        try:
            text = extract_pdf_text(downloaded.content)
            text_hash = sha256_text(text)
            text_chars = len(text)
            if text_chars == 0:
                extraction_error = "extracted text was empty"
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            extraction_error = f"{type(exc).__name__}: {exc}"
            warnings.append(f"text extraction failed for {name}: {extraction_error}")

        sources.append(
            DriveSourceSnapshot(
                file_id=downloaded.file_id,
                name=downloaded.name,
                mime_type=downloaded.mime_type,
                size_bytes=downloaded.size_bytes,
                modified_time=downloaded.modified_time,
                version=downloaded.version,
                head_revision_id=downloaded.head_revision_id,
                content_sha256=content_hash,
                text_sha256=text_hash,
                text_chars=text_chars,
                extraction_error=extraction_error,
                customer_ids=tuple(customer_ids),
            )
        )

    missing = sorted(expected - seen_names)
    for name in missing:
        warnings.append(f"registry expects {name} but it is not in the Drive folder")
    for name in unclaimed:
        warnings.append(f"{name} is in the Drive folder but no registry customer claims it")

    ordered = tuple(sorted(sources, key=lambda source: source.file_id))
    digest = corpus_digest(
        {
            "file_id": source.file_id,
            "version": source.version or source.head_revision_id,
            "content_sha256": source.content_sha256,
            "text_sha256": source.text_sha256,
        }
        for source in ordered
    )

    return EvidenceCorpusSnapshot(
        folder_id=drive.folder_id,
        source_count=len(ordered),
        sources=ordered,
        corpus_digest=digest,
        missing_documents=tuple(missing),
        unclaimed_documents=tuple(sorted(unclaimed)),
    )


def build_analysis_snapshot(
    reference: str,
    settings: Settings,
    registry: CustomerRegistry,
    github: GitHubReader,
    drive: DriveReader,
) -> AnalysisSnapshot:
    """Read one pull request and its evidence, and pin it all to one record."""
    warnings: list[str] = []

    ref = github.resolve_reference(reference)
    owner, repo, number = ref.owner, ref.repo, ref.number

    repository = github.get_repository(owner, repo)
    pull_request = github.get_pull_request(owner, repo, number)
    base_sha = pull_request["base"]["sha"]
    head_sha = pull_request["head"]["sha"]

    # Supported configuration is always fetched at both revisions, whether or
    # not the pull request touched it. Effective values are needed at both ends.
    base_config_bytes: dict[str, bytes | None] = {}
    head_config_bytes: dict[str, bytes | None] = {}
    for path in SUPPORTED_RETENTION_PATHS:
        base_config_bytes[path] = github.get_file_at_ref(owner, repo, path, base_sha)
        head_config_bytes[path] = github.get_file_at_ref(owner, repo, path, head_sha)

    changed_files: list[GitHubFileSnapshot] = []
    for entry in github.list_changed_files(owner, repo, number):
        path = entry["filename"]
        surface = classify_path(path)

        base_hash: str | None = None
        head_hash: str | None = None
        if surface in FETCH_CONTENT_FOR:
            base_bytes = base_config_bytes.get(path)
            if base_bytes is None and path not in base_config_bytes:
                base_bytes = github.get_file_at_ref(owner, repo, path, base_sha)
            head_bytes = head_config_bytes.get(path)
            if head_bytes is None and path not in head_config_bytes:
                head_bytes = github.get_file_at_ref(owner, repo, path, head_sha)
            base_hash = sha256_bytes(base_bytes) if base_bytes is not None else None
            head_hash = sha256_bytes(head_bytes) if head_bytes is not None else None

        if surface is SurfaceClass.UNKNOWN_RETENTION_RELATED:
            warnings.append(
                f"{path} looks retention related but its schema is not supported. "
                f"it must not be treated as safe"
            )

        changed_files.append(
            GitHubFileSnapshot(
                path=path,
                status=entry.get("status", "unknown"),
                additions=int(entry.get("additions", 0)),
                deletions=int(entry.get("deletions", 0)),
                previous_path=entry.get("previous_filename"),
                surface=surface,
                base_content_sha256=base_hash,
                head_content_sha256=head_hash,
            )
        )

    base_config = _parse_or_warn(base_config_bytes, "base", warnings)
    head_config = _parse_or_warn(head_config_bytes, "head", warnings)

    evidence = build_evidence_corpus(drive, registry, warnings)

    return AnalysisSnapshot(
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        policy_version=POLICY_VERSION,
        parser_version=CONFIG_PARSER_VERSION,
        created_at=datetime.now(timezone.utc),
        repository=f"{owner}/{repo}",
        repository_id=repository.get("id"),
        pr_number=number,
        pr_title=pull_request.get("title", ""),
        pr_url=pull_request.get("html_url", ""),
        target_branch=pull_request["base"]["ref"],
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=tuple(changed_files),
        supported_changed_surfaces=_paths(changed_files, SurfaceClass.SUPPORTED_RETENTION),
        unknown_retention_changed_surfaces=_paths(
            changed_files, SurfaceClass.UNKNOWN_RETENTION_RELATED
        ),
        unsupported_changed_surfaces=_paths(changed_files, SurfaceClass.UNSUPPORTED),
        customer_cohort=tuple(registry.customer_ids()),
        base_effective_config=resolve_cohort(base_config, registry),
        head_effective_config=resolve_cohort(head_config, registry),
        evidence_corpus=evidence,
        collection_warnings=tuple(warnings),
    )


def _paths(files: list[GitHubFileSnapshot], surface: SurfaceClass) -> tuple[str, ...]:
    return tuple(sorted(f.path for f in files if f.surface is surface))


def _parse_or_warn(
    contents: dict[str, bytes | None], label: str, warnings: list[str]
) -> RetentionConfig | None:
    """Parse the supported retention configuration, recording any failure."""
    for path in SUPPORTED_RETENTION_PATHS:
        raw = contents.get(path)
        if raw is None:
            warnings.append(f"{path} is absent at the {label} revision")
            return None
        try:
            return parse_retention_config(raw.decode("utf-8"))
        except (ConfigError, UnicodeDecodeError) as exc:
            warnings.append(f"{path} at the {label} revision could not be parsed: {exc}")
            return None
    return None
