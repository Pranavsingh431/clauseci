"""
Presentation integrity for the Evidence Console.

Not a UI test suite. These check that the console tells the truth: that it reads
generated evidence rather than hardcoded numbers, and that it never claims
something the system does not do.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "ui" / "console.py"
EVIDENCE = ROOT / "evals" / "results" / "hero-lifecycle.json"
SUMMARY = ROOT / "evals" / "results" / "latest-summary.json"

UNSAFE_SHA = "ed423b0bbb81d3d0828c27613d484704db701ed5"
CORRECTED_SHA = "a47f5657e69908002b421748a03ac3a5bbf92284"


@pytest.fixture(scope="module")
def evidence() -> dict:
    return json.loads(EVIDENCE.read_text())


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads(SUMMARY.read_text())


def test_the_console_imports():
    import ast
    ast.parse(CONSOLE.read_text())


def test_public_evidence_and_summary_exist_and_parse(evidence, summary):
    assert evidence["case_id"]
    assert summary["metrics"]


def test_the_two_commits_are_distinct(evidence):
    assert evidence["unsafe"]["head_sha"] != evidence["corrected"]["head_sha"]
    assert evidence["unsafe"]["head_sha"] == UNSAFE_SHA
    assert evidence["corrected"]["head_sha"] == CORRECTED_SHA


def test_the_unsafe_commit_is_a_conflict(evidence):
    assert evidence["unsafe"]["decision"] == "CONFLICT"
    violated = [f for f in evidence["unsafe"]["findings"] if f["disposition"] == "VIOLATED"]
    assert {f["category"] for f in violated} == {"application_logs", "diagnostic_logs"}
    assert all(f["customer_id"] == "acme-corp" for f in violated)


def test_the_corrected_commit_passes(evidence):
    assert evidence["corrected"]["decision"] == "PASS_SCOPED"
    assert not [f for f in evidence["corrected"]["findings"]
                if f["disposition"] == "VIOLATED"]


def test_one_case_spans_both_analyses(evidence):
    assert evidence["invariants"]["one_case_many_analyses"] is True
    assert evidence["invariants"]["distinct_analysis_ids"] is True
    assert evidence["invariants"]["single_slack_resource"] is True
    assert evidence["case_state"] == "RESOLVED"
    assert len({a["head_sha"] for a in evidence["analyses"]}) == 2


def test_an_unrepresented_category_keeps_no_limit(evidence):
    labs_audit = next(f for f in evidence["unsafe"]["findings"]
                      if f["customer_id"] == "acme-labs" and f["category"] == "audit_logs")
    assert labs_audit["disposition"] == "NO_REPRESENTED_OBLIGATION"
    assert labs_audit["represented_limit"] is None


def test_the_false_green_metric_is_zero_of_eleven(summary):
    false_green = summary["metrics"]["false_green"]
    assert false_green["count"] == 0
    assert false_green["unsafe_or_unresolved_cases"] == 11


def test_the_console_reads_metrics_instead_of_hardcoding_them():
    source = CONSOLE.read_text()
    assert "latest-summary.json" in source
    assert "hero-lifecycle.json" in source
    # the headline ratios must be interpolated, never typed in
    for literal in ('"0 / 11"', "'0 / 11'", '"5 / 5"', '"2 / 2"', '"10 / 10"'):
        assert literal not in source, f"the console hardcodes {literal}"


def test_the_console_separates_regression_tests_from_evaluation():
    source = CONSOLE.read_text()
    assert "regression tests" in source
    assert "never added together" in source


def test_the_console_never_claims_auto_remediation():
    source = CONSOLE.read_text()
    for banned in ("auto-remediat", "automatically fixed", "fixed automatically",
                   "ClauseCI pushed", "ClauseCI merged", "ClauseCI committed"):
        assert banned not in source, f"the console claims {banned}"
    assert "did not push or merge code" in source


def test_the_console_never_claims_universal_compliance():
    source = CONSOLE.read_text()
    assert "not a statement of legal compliance" in source
    for banned in ("fully compliant", "legally compliant", "guaranteed",
                   "all contracts", "zero risk"):
        assert banned not in source, f"the console claims {banned}"


def test_exactly_once_appears_only_as_a_denial():
    """
    The phrase is allowed, but only where the console denies it. Banning the
    words outright would delete an honest disclaimer.
    """
    # collapse adjacent string literals and whitespace, because the sentence is
    # written across two source lines
    flat = re.sub(r'"\s*\n\s*"', "", CONSOLE.read_text())
    flat = " ".join(flat.split())
    for match in re.finditer(r"exactly once", flat):
        window = flat[max(0, match.start() - 40):match.end() + 40]
        assert "not exactly once" in window, f"unqualified claim near: {window}"


def test_the_console_shows_no_gmail_workflow():
    source = CONSOLE.read_text()
    assert "gmail" not in source.lower()


def test_the_console_embeds_no_secret():
    source = CONSOLE.read_text()
    secret = re.compile(r"xoxb-[0-9]|github_pat_[A-Za-z0-9_]{10,}|sk-or-v1-[a-f0-9]{10,}")
    assert not secret.search(source)
    assert "/Users/" not in source


def test_the_public_evidence_carries_no_raw_provider_identifier():
    """
    Matches the shape of a Slack identifier rather than naming the real one, so
    this file does not itself publish the value it is guarding.
    """
    body = EVIDENCE.read_text()
    slack_channel = re.compile(r"\bC0[A-Z0-9]{8,}\b")
    slack_timestamp = re.compile(r"\b1[0-9]{9}\.[0-9]{6}\b")
    assert not slack_channel.search(body), "a raw Slack channel id reached the artifact"
    assert not slack_timestamp.search(body), "a raw Slack message ts reached the artifact"
    evidence = json.loads(body)
    assert evidence["slack_resource"].startswith("res-")
    for analysis in evidence["analyses"]:
        for effect in analysis["effects"]:
            resource = effect.get("provider_resource_id")
            assert resource is None or resource.startswith("res-")


def test_the_public_evidence_keeps_what_matters_as_evidence(evidence):
    """Sanitisation must not remove the things a judge needs to check."""
    assert evidence["unsafe"]["corpus_digest"]
    assert evidence["corrected"]["corpus_digest"]
    assert evidence["unsafe"]["semantic_model"]
    assert evidence["correction"]["patch"]
    assert evidence["unsafe_receipt"]["execution_state"] == "VERIFIED"
    assert evidence["corrected_receipt"]["execution_state"] == "VERIFIED"


def test_the_preferred_candidate_reports_its_denominators(evidence):
    candidate = next(c for c in evidence["unsafe"]["candidates"]
                     if c["candidate_id"] == evidence["unsafe"]["preferred_candidate_id"])
    assert candidate["requested_outcomes_preserved"] == 4
    assert candidate["requested_outcomes_total"] == 6
    assert candidate["customers_fully_preserved"] == 2
    assert candidate["customers_total"] == 3
