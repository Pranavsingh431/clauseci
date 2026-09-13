"""
Tests that the hero scenario fixture is internally consistent.

These compare the configuration snapshots against the hand written oracle in
evals/ground_truth/. They are the Phase 1 exit criteria, expressed as code.
"""

from pathlib import Path

import pytest
import yaml

from clauseci.config_resolution import diff_effective, resolve_all

ROOT = Path(__file__).resolve().parent.parent
JUDGED_FIELDS = ("application_logs_days", "diagnostic_logs_days", "audit_logs_days")


def effective(config) -> dict[str, dict[str, int]]:
    return {
        cid: {f: v.value for f, v in fields.items() if f in JUDGED_FIELDS}
        for cid, fields in resolve_all(config).items()
    }


# ------------------------------------------------ snapshots match the oracle

def test_baseline_matches_oracle(baseline, oracle):
    assert effective(baseline) == oracle["baseline_effective"]


def test_requested_matches_oracle(requested, oracle):
    assert effective(requested) == oracle["requested_effective"]


def test_correction_matches_oracle(correction, oracle):
    assert effective(correction) == oracle["preferred_correction_effective"]


# ------------------------------------------------ exit criterion 3

def test_requested_state_is_ninety_for_all_three_customers(requested, oracle):
    got = effective(requested)
    assert set(got) == set(oracle["customers"])
    for customer_id in oracle["customers"]:
        assert got[customer_id]["application_logs_days"] == 90
        assert got[customer_id]["diagnostic_logs_days"] == 90


# ------------------------------------------------ exit criterion 4

def test_preferred_correction_holds_acme_down_and_lets_others_rise(correction):
    got = effective(correction)
    assert got["acme-corp"]["application_logs_days"] == 30
    assert got["acme-corp"]["diagnostic_logs_days"] == 30
    assert got["globex"]["application_logs_days"] == 90
    assert got["globex"]["diagnostic_logs_days"] == 90
    assert got["acme-labs"]["application_logs_days"] == 90
    assert got["acme-labs"]["diagnostic_logs_days"] == 90


# ------------------------------------------------ exit criterion 2

@pytest.mark.parametrize("state", ["baseline", "requested", "correction"])
def test_acme_audit_is_never_ninety(state, request):
    config = request.getfixturevalue(state)
    assert effective(config)["acme-corp"]["audit_logs_days"] != 90


def test_acme_audit_stays_at_its_cap_in_every_state(baseline, requested, correction, oracle):
    cap = oracle["customers"]["acme-corp"]["caps"]["audit_logs_days"]
    for config in (baseline, requested, correction):
        assert effective(config)["acme-corp"]["audit_logs_days"] == cap


def test_correction_pins_acme_audit_explicitly_so_defaults_cannot_raise_it(correction):
    """An inherited value would silently move the next time defaults change."""
    from clauseci.config_resolution import ValueSource, resolve_effective
    got = resolve_effective(correction, "acme-corp", "audit_logs_days")
    assert got.source is ValueSource.CUSTOMER_OVERRIDE


# ------------------------------------------------ what the pull request does

def test_pull_request_changes_only_application_and_diagnostic(baseline, requested):
    changes = diff_effective(baseline, requested)
    changed_fields = {c.field for c in changes}
    assert changed_fields == {"application_logs_days", "diagnostic_logs_days"}


def test_pull_request_affects_all_three_customers(baseline, requested, oracle):
    changes = diff_effective(baseline, requested)
    assert {c.customer_id for c in changes} == set(oracle["customers"])


def test_every_pull_request_change_is_an_increase(baseline, requested):
    assert all(c.is_increase for c in diff_effective(baseline, requested))


def test_audit_is_untouched_by_the_pull_request(baseline, requested):
    changes = diff_effective(baseline, requested)
    assert not [c for c in changes if c.field == "audit_logs_days"]


# ------------------------------------------------ exit criterion 5

def test_acme_corp_and_acme_labs_are_separate_identities(registry):
    by_id = {c["customer_id"]: c for c in registry["customers"]}
    assert "acme-corp" in by_id and "acme-labs" in by_id
    assert by_id["acme-corp"]["legal_entity_name"] != by_id["acme-labs"]["legal_entity_name"]
    assert by_id["acme-corp"]["config_key"] != by_id["acme-labs"]["config_key"]


def test_the_two_acme_entities_share_no_documents(registry):
    by_id = {c["customer_id"]: set(c["eligible_documents"]) for c in registry["customers"]}
    assert by_id["acme-corp"].isdisjoint(by_id["acme-labs"])


def test_non_affiliation_is_declared_in_both_directions(registry):
    by_id = {c["customer_id"]: c for c in registry["customers"]}
    assert "acme-labs" in by_id["acme-corp"]["not_affiliated_with"]
    assert "acme-corp" in by_id["acme-labs"]["not_affiliated_with"]


def test_every_registry_customer_exists_in_the_configuration(registry, baseline):
    config_keys = {c["config_key"] for c in registry["customers"]}
    assert config_keys == set(baseline.customer_ids())


# ------------------------------------------------ exit criterion 6

@pytest.mark.parametrize(
    "document",
    [
        "02_Acme_DPA_2025.pdf",                          # superseded
        "03_Acme_DPA_Amendment_2026_SIGNED.pdf",         # controlling
        "06_Acme_DPA_Amendment_2026_UNSIGNED_DRAFT.pdf", # newer but unsigned
        "07_Acme_Security_Addendum_2026.pdf",            # prompt injection
        "08_AcmeLabs_MSA_2026.pdf",                      # similar name, other entity
    ],
)
def test_adversarial_documents_are_still_present(document):
    assert (ROOT / "demo_contracts" / document).is_file()


def test_adversarial_documents_are_still_eligible_for_acme(registry):
    """Inconvenient documents must stay in scope. Deciding is the analyzer's job."""
    acme = next(c for c in registry["customers"] if c["customer_id"] == "acme-corp")
    for document in (
        "02_Acme_DPA_2025.pdf",
        "06_Acme_DPA_Amendment_2026_UNSIGNED_DRAFT.pdf",
        "07_Acme_Security_Addendum_2026.pdf",
    ):
        assert document in acme["eligible_documents"]


# ------------------------------------------------ exit criterion 7

FORBIDDEN_IN_REGISTRY = ("cap", "max_", "expected", "verdict", "compliant", "_days")


def test_registry_contains_no_caps_or_expected_answers(registry):
    text = yaml.dump(registry).lower()
    for token in FORBIDDEN_IN_REGISTRY:
        assert token not in text, f"registry leaks an answer: {token!r}"


def test_runtime_package_never_reads_the_oracle():
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        assert "ground_truth" not in body, f"{path} reads the oracle"
        assert "hero_retention" not in body, f"{path} reads the oracle"


def test_resolver_hardcodes_no_scenario_values():
    body = (ROOT / "clauseci" / "config_resolution.py").read_text()
    for customer_id in ("acme-corp", "globex", "acme-labs"):
        assert customer_id not in body


# ------------------------------------------------ drift guards

def test_correction_snapshot_matches_the_remediation_file():
    snapshot = (ROOT / "tests" / "fixtures" / "retention_correction.yaml").read_text()
    source = (ROOT / "prep" / "remediation" / "retention.yaml").read_text()
    assert snapshot == source


def test_oracle_caps_are_supported_by_the_contract_text(oracle):
    """Checks the oracle itself against the source PDFs."""
    from pypdf import PdfReader

    expectations = {
        "acme-corp": ("03_Acme_DPA_Amendment_2026_SIGNED.pdf", "thirty (30) days"),
        "globex": ("05_Globex_DPA_2026.pdf", "ninety (90) days"),
        "acme-labs": ("08_AcmeLabs_MSA_2026.pdf", "one hundred and eighty (180) days"),
    }
    for customer_id, (filename, phrase) in expectations.items():
        assert oracle["customers"][customer_id]["controlling_document"] == filename
        pdf = ROOT / "demo_contracts" / filename
        text = " ".join(
            " ".join((page.extract_text() or "") for page in PdfReader(pdf).pages).split()
        )
        assert phrase in text.lower(), f"{filename} does not state {phrase!r}"


def test_acme_labs_has_no_audit_clause_so_audit_is_out_of_scope(oracle):
    assert oracle["customers"]["acme-labs"]["caps"]["audit_logs_days"] is None
