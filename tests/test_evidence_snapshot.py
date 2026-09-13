"""
Phase 2 tests: deterministic evidence snapshots.

Everything here runs offline against local fixtures. The one live check against
the real providers is run separately and reported, not asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clauseci.adapters.github import (
    PullRequestReferenceError,
    RepositoryNotAllowed,
    parse_pr_reference,
)
from clauseci.config_resolution import (
    ValueSource,
    parse_retention_config,
    resolve_effective,
)
from clauseci.domain.classification import SurfaceClass, classify_path
from clauseci.domain.digests import canonical_digest, corpus_digest, sha256_bytes
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.snapshot import build_analysis_snapshot
from clauseci.registry import load_registry
from clauseci.settings import SUPPORTED_RETENTION_PATHS
from clauseci.versions import RETENTION_STATUS_CONTEXT, SMOKE_TEST_STATUS_CONTEXT
from tests.fakes import BASE_SHA, HEAD_SHA, FakeDriveReader, FakeGitHubReader

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def snapshot():
    return build_analysis_snapshot(
        "https://github.com/Pranavsingh431/clauseci-demo-saas/pull/1",
        settings=None,
        registry=load_registry(),
        github=FakeGitHubReader(),
        drive=FakeDriveReader(),
    )


# ---------------------------------------------------------------- 1. PR parsing

@pytest.mark.parametrize(
    "reference,expected_number",
    [
        ("https://github.com/o/r/pull/7", 7),
        ("http://github.com/o/r/pull/7", 7),
        ("https://www.github.com/o/r/pull/7/files", 7),
        ("o/r#7", 7),
        ("7", 7),
        ("#7", 7),
    ],
)
def test_pull_request_references_parse(reference, expected_number):
    assert parse_pr_reference(reference).number == expected_number


def test_pull_request_url_yields_owner_and_repo():
    ref = parse_pr_reference("https://github.com/acme/widgets/pull/42")
    assert (ref.owner, ref.repo, ref.number) == ("acme", "widgets", 42)


def test_bare_number_carries_no_repository():
    ref = parse_pr_reference("42")
    assert ref.owner is None and ref.repo is None


@pytest.mark.parametrize("bad", ["", "   ", "not a reference", "https://github.com/o/r", "o/r"])
def test_unparseable_references_raise(bad):
    with pytest.raises(PullRequestReferenceError):
        parse_pr_reference(bad)


# ------------------------------------------------------------- 2. allowlist

def test_reference_outside_the_allowlist_is_refused():
    reader = FakeGitHubReader()
    with pytest.raises(RepositoryNotAllowed):
        reader.resolve_reference("https://github.com/someone/else/pull/1")


def test_reference_inside_the_allowlist_is_accepted():
    reader = FakeGitHubReader()
    ref = reader.resolve_reference("https://github.com/Pranavsingh431/clauseci-demo-saas/pull/1")
    assert ref.number == 1


# ------------------------------------------------- 3, 4, 5. binding and fetch

def test_base_and_head_shas_are_pinned(snapshot):
    assert snapshot.base_sha == BASE_SHA
    assert snapshot.head_sha == HEAD_SHA
    assert snapshot.base_sha != snapshot.head_sha


def test_changed_file_inventory_is_complete():
    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
        {"filename": "config/cache.yaml", "status": "added", "additions": 9, "deletions": 0},
    ])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    assert {f.path for f in snap.changed_files} == {
        "config/retention.yaml", "README.md", "config/cache.yaml"
    }


def test_supported_config_is_fetched_at_both_explicit_shas():
    github = FakeGitHubReader()
    build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    for path in SUPPORTED_RETENTION_PATHS:
        assert (path, BASE_SHA) in github.fetched
        assert (path, HEAD_SHA) in github.fetched


def test_supported_config_is_fetched_even_when_the_pr_did_not_touch_it():
    """Effective values are needed at both ends regardless of the diff."""
    github = FakeGitHubReader(changed_files=[
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
    ])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    assert (SUPPORTED_RETENTION_PATHS[0], BASE_SHA) in github.fetched
    assert snap.base_effective_config and snap.head_effective_config


def test_whole_file_content_is_digested_not_just_the_diff(snapshot):
    changed = next(f for f in snapshot.changed_files if f.path == "config/retention.yaml")
    assert changed.base_content_sha256 and changed.head_content_sha256
    assert changed.base_content_sha256 != changed.head_content_sha256


# ---------------------------------------------------- 6, 7, 8. classification

def test_known_retention_file_is_supported():
    assert classify_path("config/retention.yaml") is SurfaceClass.SUPPORTED_RETENTION


@pytest.mark.parametrize("path", ["README.md", "config/cache.yaml", "api/public_endpoints.yaml"])
def test_unrelated_files_are_unsupported(path):
    assert classify_path(path) is SurfaceClass.UNSUPPORTED


@pytest.mark.parametrize(
    "path",
    ["config/retention_v2.yaml", "services/log_retention.json", "config/ttl.yaml", "data_lifecycle.yaml"],
)
def test_retention_like_unknown_schema_is_never_silently_safe(path):
    assert classify_path(path) is SurfaceClass.UNKNOWN_RETENTION_RELATED
    assert classify_path(path) is not SurfaceClass.UNSUPPORTED
    assert classify_path(path) is not SurfaceClass.SUPPORTED_RETENTION


def test_unknown_retention_surface_raises_a_warning_in_the_snapshot():
    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "config/retention_v2.yaml", "status": "added", "additions": 5, "deletions": 0},
    ])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    assert snap.unknown_retention_changed_surfaces == ("config/retention_v2.yaml",)
    assert any("not supported" in w for w in snap.collection_warnings)


def test_surfaces_are_partitioned_with_nothing_lost():
    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "config/retention_v2.yaml", "status": "added", "additions": 5, "deletions": 0},
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
    ])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    partitioned = (
        set(snap.supported_changed_surfaces)
        | set(snap.unknown_retention_changed_surfaces)
        | set(snap.unsupported_changed_surfaces)
    )
    assert partitioned == {f.path for f in snap.changed_files}


# ------------------------------------------- 9, 10, 11. resolution behaviours

def test_default_applies_when_no_override():
    cfg = parse_retention_config(
        "defaults:\n  application_logs_days: 30\ncustomers:\n  c: {}\n"
    )
    got = resolve_effective(cfg, "c", "application_logs_days")
    assert (got.value, got.source) == (30, ValueSource.DEFAULT)
    assert got.config_path == "defaults.application_logs_days"


def test_customer_override_applies_and_records_its_path():
    cfg = parse_retention_config(
        "defaults:\n  application_logs_days: 30\ncustomers:\n  c:\n    application_logs_days: 15\n"
    )
    got = resolve_effective(cfg, "c", "application_logs_days")
    assert (got.value, got.source) == (15, ValueSource.CUSTOMER_OVERRIDE)
    assert got.config_path == "customers.c.application_logs_days"


def test_removed_override_falls_back_to_the_default():
    with_override = parse_retention_config(
        "defaults:\n  application_logs_days: 30\ncustomers:\n  c:\n    application_logs_days: 15\n"
    )
    without = parse_retention_config(
        "defaults:\n  application_logs_days: 30\ncustomers:\n  c: {}\n"
    )
    assert resolve_effective(with_override, "c", "application_logs_days").value == 15
    after = resolve_effective(without, "c", "application_logs_days")
    assert after.value == 30
    assert after.source is ValueSource.DEFAULT


def test_explicit_null_override_falls_back_and_is_recorded_distinctly():
    cfg = parse_retention_config(
        "defaults:\n  application_logs_days: 30\ncustomers:\n  c:\n    application_logs_days: null\n"
    )
    got = resolve_effective(cfg, "c", "application_logs_days")
    assert got.value == 30
    assert got.source is ValueSource.DEFAULT_AFTER_EXPLICIT_NULL


def test_every_resolved_value_carries_its_unit(snapshot):
    for customer in snapshot.base_effective_config:
        for value in customer.values:
            assert value.unit == "days"


# ------------------------------------------------- 12, 13, 14. hero values

HERO_BASE = {
    "acme-corp": {"application_logs": 30, "diagnostic_logs": 30, "audit_logs": 30},
    "globex": {"application_logs": 30, "diagnostic_logs": 30, "audit_logs": 365},
    "acme-labs": {"application_logs": 30, "diagnostic_logs": 30, "audit_logs": 180},
}
HERO_HEAD = {
    "acme-corp": {"application_logs": 90, "diagnostic_logs": 90, "audit_logs": 30},
    "globex": {"application_logs": 90, "diagnostic_logs": 90, "audit_logs": 365},
    "acme-labs": {"application_logs": 90, "diagnostic_logs": 90, "audit_logs": 180},
}


def as_mapping(customer_snapshots) -> dict[str, dict[str, int | None]]:
    return {
        c.customer_id: {v.category: v.effective_value for v in c.values}
        for c in customer_snapshots
    }


def test_hero_base_effective_values(snapshot):
    assert as_mapping(snapshot.base_effective_config) == HERO_BASE


def test_hero_head_effective_values(snapshot):
    assert as_mapping(snapshot.head_effective_config) == HERO_HEAD


def test_whole_cohort_is_resolved(snapshot):
    assert snapshot.customer_cohort == ("acme-corp", "globex", "acme-labs")
    assert len(snapshot.base_effective_config) == 3


def test_resolved_value_provenance_is_recorded(snapshot):
    head = {c.customer_id: c for c in snapshot.head_effective_config}
    # the pull request moved a default, so acme-corp inherits it
    assert head["acme-corp"].value_for("application_logs").source_type == "default"
    # globex pins its own audit period
    assert head["globex"].value_for("audit_logs").source_type == "customer_override"


def test_customer_missing_from_configuration_is_recorded_not_skipped():
    stripped = (
        "defaults:\n  application_logs_days: 30\n  diagnostic_logs_days: 30\n"
        "  audit_logs_days: 30\ncustomers:\n  globex: {}\n  acme-labs: {}\n"
    )
    github = FakeGitHubReader(base_config=stripped, head_config=stripped)
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    acme = next(c for c in snap.base_effective_config if c.customer_id == "acme-corp")
    assert acme.present_in_config is False
    assert all(v.effective_value is None for v in acme.values)
    assert all(v.unresolved_reason for v in acme.values)


def test_malformed_configuration_does_not_become_a_silent_pass():
    github = FakeGitHubReader(head_config="this is not: [valid yaml")
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    assert any("could not be parsed" in w for w in snap.collection_warnings)
    for customer in snap.head_effective_config:
        assert all(v.effective_value is None for v in customer.values)


# ------------------------------------------------ 15, 16, 17. hashing

def test_canonical_digest_ignores_key_order():
    assert canonical_digest({"a": 1, "b": 2}) == canonical_digest({"b": 2, "a": 1})


def test_canonical_digest_changes_with_content():
    assert canonical_digest({"a": 1}) != canonical_digest({"a": 2})


def test_sha256_of_bytes_is_stable():
    assert sha256_bytes(b"clauseci") == sha256_bytes(b"clauseci")
    assert sha256_bytes(b"clauseci") != sha256_bytes(b"clausecj")


def test_corpus_digest_is_independent_of_listing_order():
    entries = [
        {"file_id": "b", "version": "1", "content_sha256": "y", "text_sha256": "y2"},
        {"file_id": "a", "version": "1", "content_sha256": "x", "text_sha256": "x2"},
    ]
    assert corpus_digest(entries) == corpus_digest(list(reversed(entries)))


def test_corpus_digest_changes_when_source_bytes_change():
    entries = [{"file_id": "a", "version": "1", "content_sha256": "x", "text_sha256": "x2"}]
    changed = [{"file_id": "a", "version": "1", "content_sha256": "CHANGED", "text_sha256": "x2"}]
    assert corpus_digest(entries) != corpus_digest(changed)


def test_corpus_digest_ignores_filename():
    """Renaming a document must not change the digest. Bytes are what matter."""
    entries = [{"file_id": "a", "version": "1", "content_sha256": "x", "text_sha256": "x2",
                "name": "one.pdf"}]
    renamed = [{"file_id": "a", "version": "1", "content_sha256": "x", "text_sha256": "x2",
                "name": "two.pdf"}]
    assert corpus_digest(entries) == corpus_digest(renamed)


def test_snapshot_corpus_digest_is_reproducible():
    first = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), FakeDriveReader())
    second = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), FakeDriveReader())
    assert first.corpus_digest == second.corpus_digest


def test_snapshot_corpus_digest_survives_a_reordered_listing():
    ordered = FakeDriveReader()
    reversed_listing = FakeDriveReader()
    reversed_listing.list_order_reversed = True
    a = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), ordered)
    b = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), reversed_listing)
    assert a.corpus_digest == b.corpus_digest


def test_snapshot_corpus_digest_changes_when_a_document_changes():
    baseline = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), FakeDriveReader())
    tampered_drive = FakeDriveReader()
    original = (ROOT / "demo_contracts" / "03_Acme_DPA_Amendment_2026_SIGNED.pdf").read_bytes()
    tampered_drive.overrides["03_Acme_DPA_Amendment_2026_SIGNED.pdf"] = original + b"\n% edited"
    tampered = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), tampered_drive)
    assert baseline.corpus_digest != tampered.corpus_digest


# ------------------------------------------------ evidence corpus contents

def test_every_eligible_document_is_captured_with_digests(snapshot):
    corpus = snapshot.evidence_corpus
    assert corpus.source_count == 8
    for source in corpus.sources:
        assert source.file_id
        assert source.content_sha256
        assert source.text_sha256, f"{source.name} produced no text digest"
        assert source.text_chars > 300
        assert source.customer_ids


def test_adversarial_documents_stay_in_the_corpus(snapshot):
    names = {s.name for s in snapshot.evidence_corpus.sources}
    for required in (
        "02_Acme_DPA_2025.pdf",
        "03_Acme_DPA_Amendment_2026_SIGNED.pdf",
        "06_Acme_DPA_Amendment_2026_UNSIGNED_DRAFT.pdf",
        "07_Acme_Security_Addendum_2026.pdf",
        "08_AcmeLabs_MSA_2026.pdf",
    ):
        assert required in names


def test_document_association_comes_from_the_registry_not_the_filename(snapshot):
    by_name = {s.name: s for s in snapshot.evidence_corpus.sources}
    # both filenames start with "Acme" but they belong to different entities
    assert by_name["03_Acme_DPA_Amendment_2026_SIGNED.pdf"].customer_ids == ("acme-corp",)
    assert by_name["08_AcmeLabs_MSA_2026.pdf"].customer_ids == ("acme-labs",)


def test_a_missing_expected_document_is_reported():
    drive = FakeDriveReader(names=["01_Acme_MSA_2025.pdf"])
    snap = build_analysis_snapshot("1", None, load_registry(), FakeGitHubReader(), drive)
    assert snap.evidence_corpus.missing_documents
    assert any("not in the Drive folder" in w for w in snap.collection_warnings)


# ------------------------------------------------ 18. serialization

def test_snapshot_round_trips_through_json(snapshot):
    restored = AnalysisSnapshot.from_json(snapshot.to_json())
    assert restored.base_sha == snapshot.base_sha
    assert restored.head_sha == snapshot.head_sha
    assert restored.corpus_digest == snapshot.corpus_digest
    assert as_mapping(restored.head_effective_config) == as_mapping(snapshot.head_effective_config)
    assert restored.model_dump(mode="json") == snapshot.model_dump(mode="json")


def test_snapshot_json_is_valid_and_sorted(snapshot):
    parsed = json.loads(snapshot.to_json())
    assert parsed["snapshot_schema_version"]
    assert parsed["policy_version"]
    assert parsed["parser_version"]
    assert parsed["created_at"]


def test_snapshot_is_immutable(snapshot):
    with pytest.raises(Exception):
        snapshot.base_sha = "tampered"


# ------------------------------------------------ 19, 20. no oracle leakage

ORACLE_TERMS = ("controlling_document", "expected_verdict", "must_not_happen",
                "caps", "preferred_correction", "compliant")


def test_snapshot_contains_no_oracle_or_verdict(snapshot):
    body = snapshot.to_json().lower()
    for term in ORACLE_TERMS:
        assert term not in body, f"snapshot leaks oracle term {term!r}"


def test_snapshot_contains_no_contract_cap_values(snapshot):
    """A cap is a contract fact. Phase 2 records configuration, not caps."""
    payload = snapshot.model_dump(mode="json")
    assert "caps" not in json.dumps(payload)
    for customer in payload["base_effective_config"]:
        for value in customer["values"]:
            assert set(value) == {
                "customer_id", "category", "effective_value", "unit",
                "source_type", "config_path", "unresolved_reason",
            }


def test_runtime_package_never_imports_or_reads_the_oracle():
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        assert "ground_truth" not in body, f"{path} references the oracle"
        assert "hero_retention" not in body, f"{path} references the oracle"
        assert "evals" not in body, f"{path} references the evaluation package"


#: The only two modules allowed to mutate an external provider. Every other
#: runtime module must stay read only, and a test enforces that.
WRITE_ADAPTERS = {"github_write.py", "slack_write.py"}


def test_write_capability_lives_only_in_the_designated_adapters():
    """
    Phase 5 added writes. The invariant is now containment, not absence.

    Any mutating provider call outside the two write adapters is a defect.
    """
    banned = ("chat_postMessage", "chat_update", "drafts().create", "drafts.create",
              "/statuses/", "session.post", "session.put", "session.patch",
              "session.delete")
    offenders = []
    for path in (ROOT / "clauseci").rglob("*.py"):
        if path.name in WRITE_ADAPTERS:
            continue
        body = path.read_text()
        for token in banned:
            if token in body:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, f"mutating calls outside the write adapters: {offenders}"


def test_the_write_adapters_actually_contain_the_write_capability():
    """Guards that the test above is not passing because writes moved elsewhere."""
    github = (ROOT / "clauseci" / "adapters" / "github_write.py").read_text()
    slack = (ROOT / "clauseci" / "adapters" / "slack_write.py").read_text()
    assert "/statuses/" in github
    assert "chat_postMessage" in slack


def test_no_gmail_write_capability_exists_anywhere_in_the_runtime():
    """Gmail is verified infrastructure. It is not part of the workflow."""
    banned = ('build("gmail"', "users().drafts", "drafts().create", "drafts().send",
              "EmailMessage")
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        for token in banned:
            assert token not in body, f"{path.name} contains a Gmail operation: {token}"


# ------------------------------------------------ status context

def test_status_context_is_defined_once_and_matches_branch_protection():
    assert RETENTION_STATUS_CONTEXT == "ClauseCI / retention-compliance"
    assert RETENTION_STATUS_CONTEXT != SMOKE_TEST_STATUS_CONTEXT
