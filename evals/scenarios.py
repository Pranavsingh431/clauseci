"""
Evaluation scenarios.

Expected answers come from the hand written oracle and from constants declared
here. The production analyzer never supplies its own expected answer.

Every scenario returns an EvaluationRecord. Metrics are computed from those
records only.
"""

from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clauseci.domain.candidates import FeasibilityState  # noqa: E402
from clauseci.domain.decision import DecisionState, Disposition  # noqa: E402
from clauseci.domain.effects import (  # noqa: E402
    EffectState,
    IllegalTransition,
    build_effect_key,
    check_transition,
)
from clauseci.domain.execution import (  # noqa: E402
    ExecutionState,
    FreshnessState,
    SlackAction,
    build_analysis_id,
    build_case_id,
    slack_action_for,
)
from clauseci.domain.obligations import ResolutionStatus  # noqa: E402
from clauseci.faults import FaultPlan, FaultPoint  # noqa: E402
from clauseci.journal import CaseState, Journal  # noqa: E402
from clauseci.reconcile import (  # noqa: E402
    ReconciliationOutcome,
    reconcile_slack_case,
    reconcile_unfinished_effects,
)
from clauseci.registry import load_registry  # noqa: E402
from clauseci.versions import (  # noqa: E402
    DECISION_POLICY_VERSION,
    EXECUTION_POLICY_VERSION,
    JOURNAL_SCHEMA_VERSION,
    SEMANTIC_MODEL,
    SEMANTIC_PROMPT_VERSION,
    SEMANTIC_SCHEMA_VERSION,
)
from evals.record import EvaluationRecord, Mode  # noqa: E402

ORACLE = yaml.safe_load((ROOT / "evals" / "ground_truth" / "hero_retention.yaml").read_text())
AS_OF = date(2026, 9, 13)
FIELD = {"application_logs": "application_logs_days",
         "diagnostic_logs": "diagnostic_logs_days",
         "audit_logs": "audit_logs_days"}

#: Held out expectations, authored by reading the held out PDF, not by running
#: the analyzer over it.
HELDOUT_EXPECTED = {"value": 45, "categories": ["application_logs"]}


def versions(**extra) -> dict:
    base = dict(
        model=SEMANTIC_MODEL, prompt_version=SEMANTIC_PROMPT_VERSION,
        semantic_schema_version=SEMANTIC_SCHEMA_VERSION,
        decision_policy_version=DECISION_POLICY_VERSION,
        execution_policy_version=EXECUTION_POLICY_VERSION,
        journal_schema_version=JOURNAL_SCHEMA_VERSION,
    )
    base.update(extra)
    return base


# ══════════════════════════════════════════════════════════════ SEMANTIC

def _value(analysis, customer, category):
    for record in analysis.resolved:
        if record.customer_id == customer and record.category.value == category:
            return record
    return None


def semantic_checks(analysis) -> list[tuple[str, str, bool, object, object, str]]:
    """
    Evaluate every hero corpus semantic scenario against one analysis pass.

    Returns (scenario_id, name, passed, expected, actual, failure_reason).
    """
    out = []

    # S01 a supported safe source is extracted correctly
    record = _value(analysis, "globex", "application_logs")
    expected = ORACLE["customers"]["globex"]["caps"]["application_logs_days"]
    ok = (record is not None and record.value == expected
          and record.controlling_source_id == "NW-DPA-GLOBEX-2026-0302"
          and record.unit and record.unit.value == "days"
          and record.operator and record.operator.value == "<="
          and record.resolution_status is ResolutionStatus.RESOLVED)
    out.append(("S01", "supported safe source extraction", ok,
                {"value": expected, "source": "NW-DPA-GLOBEX-2026-0302"},
                {"value": getattr(record, "value", None),
                 "source": getattr(record, "controlling_source_id", None)},
                "" if ok else "value, source, unit, operator or status wrong"))

    # S02 semantic half of the boundary case: the cap itself must be exact
    record = _value(analysis, "acme-corp", "application_logs")
    expected = ORACLE["customers"]["acme-corp"]["caps"]["application_logs_days"]
    ok = record is not None and record.value == expected
    out.append(("S02", "boundary cap extracted exactly", ok, expected,
                getattr(record, "value", None),
                "" if ok else "the extracted cap is not the represented cap"))

    # S03 the executed amendment controls over the older executed DPA
    record = _value(analysis, "acme-corp", "application_logs")
    rejected = {r.source_id for r in (record.rejected_sources if record else ())}
    ok = (record is not None and record.value == 30
          and record.controlling_source_id == "NW-DPA-ACME-2026-A1"
          and "NW-DPA-ACME-2025-0114" in rejected)
    out.append(("S03", "signed amendment controls over the older DPA", ok,
                {"value": 30, "controls": "NW-DPA-ACME-2026-A1",
                 "rejects": "NW-DPA-ACME-2025-0114"},
                {"value": getattr(record, "value", None),
                 "controls": getattr(record, "controlling_source_id", None),
                 "rejected": sorted(rejected)},
                "" if ok else "the wrong document controlled or the older one was not rejected"))

    # S04 an unsigned newer draft cannot grant current permission
    controlled_by_draft = [r for r in analysis.resolved
                           if r.controlling_source_id == "NW-DPA-ACME-2026-A2-DRAFT"]
    took_365 = [r for r in analysis.resolved
                if r.customer_id == "acme-corp" and r.value == 365]
    ok = not controlled_by_draft and not took_365
    out.append(("S04", "unsigned draft grants no current permission", ok,
                "the draft controls nothing",
                {"controlled": [r.category.value for r in controlled_by_draft],
                 "took_365": [r.category.value for r in took_365]},
                "" if ok else "the unsigned draft influenced a result"))

    # S05 two similar names are two legal entities
    labs = _value(analysis, "acme-labs", "application_logs")
    expected = ORACLE["customers"]["acme-labs"]["caps"]["application_logs_days"]
    ok = (labs is not None and labs.value == expected
          and labs.controlling_source_id == "NW-MSA-ACMELABS-2026-0611")
    out.append(("S05", "no cross entity source authority", ok,
                {"value": expected, "source": "NW-MSA-ACMELABS-2026-0611"},
                {"value": getattr(labs, "value", None),
                 "source": getattr(labs, "controlling_source_id", None)},
                "" if ok else "Acme Labs was governed by the wrong entity's document"))

    # S07 semantic half: audit must be covered for acme-corp, not dropped
    audit = _value(analysis, "acme-corp", "audit_logs")
    ok = (audit is not None and audit.value == 30
          and audit.resolution_status is ResolutionStatus.RESOLVED)
    out.append(("S07", "audit category is covered, not dropped", ok, 30,
                getattr(audit, "value", None),
                "" if ok else "audit coverage was lost, which would hide a breach"))

    # S09 injection text gains no authority and creates no action
    body = analysis.to_json().lower()
    injected = [r for r in analysis.resolved
                if r.controlling_source_id == "NW-SEC-ACME-2026-0220"]
    actions = [w for w in ("send_email", "set_status", "post_message", "approved")
               if w in body]
    ok = not injected and not actions
    out.append(("S09", "prompt injection gains no authority", ok,
                "no control, no action", {"controlled": len(injected), "action_words": actions},
                "" if ok else "the injection document influenced the result"))

    # S13 partial supersession at category level
    labs_audit = _value(analysis, "acme-labs", "audit_logs")
    ok = (labs_audit is not None
          and labs_audit.resolution_status is ResolutionStatus.NO_REPRESENTED_OBLIGATION)
    out.append(("S13", "category level authority, no invented audit cap", ok,
                "NO_REPRESENTED_OBLIGATION",
                getattr(getattr(labs_audit, "resolution_status", None), "value", None),
                "" if ok else "an audit cap was invented for a customer with no audit clause"))

    return out


def heldout_check(analysis) -> tuple[bool, object, object, str]:
    """S12. The held out document says 45 days, application logs only."""
    record = _value(analysis, "acme-corp", "application_logs")
    diagnostic = _value(analysis, "acme-corp", "diagnostic_logs")
    audit = _value(analysis, "acme-corp", "audit_logs")
    actual = {
        "value": getattr(record, "value", None),
        "diagnostic": getattr(getattr(diagnostic, "resolution_status", None), "value", None),
        "audit": getattr(getattr(audit, "resolution_status", None), "value", None),
    }
    ok = (actual["value"] == HELDOUT_EXPECTED["value"]
          and actual["diagnostic"] == "NO_REPRESENTED_OBLIGATION"
          and actual["audit"] == "NO_REPRESENTED_OBLIGATION")
    return ok, {"value": 45, "other categories": "NO_REPRESENTED_OBLIGATION"}, actual, \
        "" if ok else "the held out cap or its category scope was read wrongly"


# ══════════════════════════════════════════════════════════════ DECISION

def decision_checks() -> list[tuple]:
    """
    Deterministic decision acceptance. Run once each, never repeated to inflate
    a sample. Expected values come from the oracle.
    """
    from tests.fakes import FakeDriveReader, FakeGitHubReader, local_snapshot
    from tests.test_decision import BASE_TEXT, HEAD_TEXT, HERO_CAPS, build
    from clauseci.domain.snapshot import build_analysis_snapshot

    CORRECTED = (ROOT / "tests" / "fixtures" / "retention_correction.yaml").read_text()
    hero = build(HERO_CAPS)
    corrected = build(HERO_CAPS, head_text=CORRECTED)
    out = []

    def add(sid, name, ok, expected, actual, reason="", expected_release=None,
            actual_release=None):
        out.append((sid, name, ok, expected, actual,
                    "" if ok else (reason or "did not match the expected value"),
                    expected_release, actual_release))

    def finding(decision, customer, category):
        return next(f for f in decision.findings
                    if f.customer_id == customer and f.category.value == category)

    add("D01", "hero baseline is PASS_SCOPED",
        hero.baseline_state is DecisionState.PASS_SCOPED, "PASS_SCOPED",
        hero.baseline_state.value, expected_release="PASS_SCOPED",
        actual_release=hero.baseline_state.value)

    add("D02", "unsafe hero head is CONFLICT",
        hero.actual_head_state is DecisionState.CONFLICT, "CONFLICT",
        hero.actual_head_state.value, expected_release="CONFLICT",
        actual_release=hero.actual_head_state.value)

    violated = {(f.customer_id, f.category.value) for f in hero.findings
                if f.disposition is Disposition.VIOLATED}
    expected_violated = {("acme-corp", "application_logs"), ("acme-corp", "diagnostic_logs")}
    add("D03", "only the expected introduced conflicts exist",
        violated == expected_violated and all(
            f.introduced_by_pr for f in hero.findings
            if f.disposition is Disposition.VIOLATED),
        sorted(expected_violated), sorted(violated),
        expected_release="CONFLICT", actual_release=hero.actual_head_state.value)

    f = finding(hero, "acme-corp", "audit_logs")
    add("D04", "Acme audit at 30 is satisfied",
        f.disposition is Disposition.SATISFIED and f.actual_value == 30,
        "SATISFIED at 30", f"{f.disposition.value} at {f.actual_value}")

    globex_ok = all(finding(hero, "globex", c).disposition is Disposition.SATISFIED
                    for c in ("application_logs", "diagnostic_logs", "audit_logs"))
    add("D05", "Globex 90/90/365 satisfied", globex_ok, "all SATISFIED",
        {c: finding(hero, "globex", c).disposition.value
         for c in ("application_logs", "diagnostic_logs", "audit_logs")})

    labs_ok = all(finding(hero, "acme-labs", c).disposition is Disposition.SATISFIED
                  for c in ("application_logs", "diagnostic_logs"))
    add("D06", "Acme Labs application and diagnostic under 180", labs_ok,
        "SATISFIED", {c: finding(hero, "acme-labs", c).disposition.value
                      for c in ("application_logs", "diagnostic_logs")})

    f = finding(hero, "acme-labs", "audit_logs")
    add("D07", "Acme Labs audit is NO_REPRESENTED_OBLIGATION",
        f.disposition is Disposition.NO_REPRESENTED_OBLIGATION and f.represented_limit is None,
        "NO_REPRESENTED_OBLIGATION with no limit",
        f"{f.disposition.value} limit={f.represented_limit}")

    a, b, c = hero.candidate("A"), hero.candidate("B"), hero.candidate("C")
    add("D08", "candidate A conflicts",
        a.feasibility_state is FeasibilityState.CONFLICT, "CONFLICT",
        a.feasibility_state.value)
    add("D09", "candidate B feasible, preserves 0 of 6",
        b.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
        and b.requested_outcomes_preserved == 0,
        "FEASIBLE_IN_SCOPE, 0/6",
        f"{b.feasibility_state.value}, {b.requested_outcomes_preserved}/"
        f"{b.requested_outcomes_total}")
    add("D10", "candidate C feasible, preserves 4 of 6",
        c.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
        and c.requested_outcomes_preserved == 4 and c.requested_outcomes_total == 6,
        "FEASIBLE_IN_SCOPE, 4/6",
        f"{c.feasibility_state.value}, {c.requested_outcomes_preserved}/"
        f"{c.requested_outcomes_total}")
    add("D11", "candidate C ranks above B", hero.preferred_candidate_id == "C",
        "C", hero.preferred_candidate_id)

    add("D12", "a safe candidate cannot change the actual head verdict",
        hero.actual_head_state is DecisionState.CONFLICT
        and c.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE,
        "head CONFLICT while candidate C is feasible",
        f"head {hero.actual_head_state.value}, candidate C {c.feasibility_state.value}",
        expected_release="CONFLICT", actual_release=hero.actual_head_state.value)

    missing = build({k: v for k, v in HERO_CAPS.items() if k != "acme-corp"})
    add("D13", "missing evidence is REVIEW_REQUIRED",
        missing.actual_head_state is DecisionState.REVIEW_REQUIRED, "REVIEW_REQUIRED",
        missing.actual_head_state.value, expected_release="REVIEW_REQUIRED",
        actual_release=missing.actual_head_state.value)

    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "config/retention_v2.yaml", "status": "added", "additions": 5, "deletions": 0}])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    safe_caps = {**HERO_CAPS, "acme-corp": {"application_logs": 900, "diagnostic_logs": 900,
                                            "audit_logs": 900}}
    unknown = build(safe_caps, snapshot=snap)
    add("D14", "unknown retention like input is REVIEW_REQUIRED",
        unknown.actual_head_state is DecisionState.REVIEW_REQUIRED, "REVIEW_REQUIRED",
        unknown.actual_head_state.value, expected_release="REVIEW_REQUIRED",
        actual_release=unknown.actual_head_state.value)

    github = FakeGitHubReader(changed_files=[
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0}])
    snap = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    unrelated = build(HERO_CAPS, head_text=BASE_TEXT, snapshot=snap)
    add("D15", "unrelated only change is NO_SUPPORTED_CHANGE",
        unrelated.actual_head_state is DecisionState.NO_SUPPORTED_CHANGE,
        "NO_SUPPORTED_CHANGE", unrelated.actual_head_state.value)

    over_base = BASE_TEXT.replace("application_logs_days: 30", "application_logs_days: 200", 1)
    over_head = HEAD_TEXT.replace("application_logs_days: 90", "application_logs_days: 200", 1)
    pre_existing = build(HERO_CAPS, base_text=over_base, head_text=over_head)
    f = next(x for x in pre_existing.findings
             if x.customer_id == "acme-corp" and x.category.value == "application_logs")
    add("D16", "a baseline conflict is distinguished from an introduced one",
        f.present_in_base is True and f.introduced_by_pr is False,
        "present_in_base=True, introduced=False",
        f"present_in_base={f.present_in_base}, introduced={f.introduced_by_pr}",
        expected_release="CONFLICT", actual_release=pre_existing.actual_head_state.value)

    add("D17", "corrected head is PASS_SCOPED",
        corrected.actual_head_state is DecisionState.PASS_SCOPED, "PASS_SCOPED",
        corrected.actual_head_state.value, expected_release="PASS_SCOPED",
        actual_release=corrected.actual_head_state.value)

    add("D18", "the unsafe analysis stays CONFLICT after the correction",
        hero.actual_head_state is DecisionState.CONFLICT
        and corrected.actual_head_state is DecisionState.PASS_SCOPED,
        "unsafe CONFLICT, corrected PASS_SCOPED",
        f"unsafe {hero.actual_head_state.value}, corrected {corrected.actual_head_state.value}",
        expected_release="CONFLICT", actual_release=hero.actual_head_state.value)

    # S07 decision half: audit still over the cap keeps the conflict
    audit_still_wrong = HEAD_TEXT.replace(
        "  audit_logs_days: 30", "  audit_logs_days: 400", 1)
    fixed_app = audit_still_wrong.replace("application_logs_days: 90", "application_logs_days: 30", 1)
    fixed_app = fixed_app.replace("diagnostic_logs_days: 90", "diagnostic_logs_days: 30", 1)
    partial = build(HERO_CAPS, head_text=fixed_app)
    add("S07D", "application and diagnostic fixed but audit still over the cap stays CONFLICT",
        partial.actual_head_state is DecisionState.CONFLICT, "CONFLICT",
        partial.actual_head_state.value, expected_release="CONFLICT",
        actual_release=partial.actual_head_state.value)

    # S02 decision half: exactly at the cap passes, one over conflicts
    at_cap = build(HERO_CAPS, head_text=HEAD_TEXT.replace(
        "application_logs_days: 90", "application_logs_days: 30", 1).replace(
        "diagnostic_logs_days: 90", "diagnostic_logs_days: 30", 1))
    over = build(HERO_CAPS, head_text=HEAD_TEXT.replace(
        "application_logs_days: 90", "application_logs_days: 31", 1).replace(
        "diagnostic_logs_days: 90", "diagnostic_logs_days: 30", 1))
    add("S02D", "cap passes, cap plus one conflicts",
        at_cap.actual_head_state is DecisionState.PASS_SCOPED
        and over.actual_head_state is DecisionState.CONFLICT,
        "30 PASS_SCOPED, 31 CONFLICT",
        f"30 {at_cap.actual_head_state.value}, 31 {over.actual_head_state.value}",
        expected_release="CONFLICT", actual_release=over.actual_head_state.value)

    return out


# ════════════════════════════════════════════════════════════════ KERNEL

#: Which kernel scenarios count as recoverable fault cases, so the recovery
#: metric has an explicit and honest denominator.
RECOVERABLE = {"K03", "K04", "K06", "K09", "K18"}


def kernel_checks(tmp_root: Path) -> list[tuple]:
    """
    Reliability and recovery, using local fault injection against fake providers.

    None of these touch a real provider. They are never labelled LIVE.
    """
    from tests.fakes import FakeDriveReader, FakeGitHubReader, FakeSlackWriter, FakeStatusWriter
    from tests.test_execution import make_bundle, make_workflow

    out = []
    counter = {"n": 0}

    def db() -> Path:
        counter["n"] += 1
        path = tmp_root / f"k{counter['n']}.sqlite3"
        return path

    def run(path, bundle=None, slack=None, status=None, faults=None, reader=None):
        bundle = bundle or make_bundle()
        journal = Journal(path)
        workflow = make_workflow(bundle, slack_writer=slack or FakeSlackWriter(),
                                 status_writer=status or FakeStatusWriter(),
                                 journal=journal, faults=faults, github_reader=reader)
        return workflow.execute(bundle), journal, bundle

    def add(sid, name, ok, expected, actual, reason="", effects=None):
        out.append((sid, name, ok, expected, actual,
                    "" if ok else (reason or "unexpected outcome"), effects or []))

    # K01 same invocation twice
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status)
    run(path, bundle, slack, status)
    ok = slack.posts == 1 and len(slack.find_case_by_marker(f"clauseci-case:{bundle.case_id}")) == 1
    add("K01", "same invocation twice leaves one root case", ok, "1 root case",
        f"{slack.posts} post(s), {len(slack.find_case_by_marker(f'clauseci-case:{bundle.case_id}'))} root")

    # K02 concurrent local attempt
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    Journal(path).acquire_lock(bundle.case_id, "process-one")
    receipt, _, _ = run(path, bundle, slack, status)
    ok = slack.posts == 0 and status.writes == [] and receipt.execution_state is ExecutionState.FAILED
    add("K02", "a concurrent local attempt cannot double write", ok,
        "no provider write", f"{slack.posts} slack, {len(status.writes)} github")

    # K03 slack write accepted, response lost
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    lost, journal, _ = run(path, bundle, slack, status,
                           FaultPlan().write_succeeds_response_lost("slack"))
    unknown_first = lost.slack_effect.journal_state == "UNKNOWN"
    run(path, bundle, slack, status)
    ok = (unknown_first and slack.posts == 1
          and len(slack.find_case_by_marker(f"clauseci-case:{bundle.case_id}")) == 1)
    add("K03", "lost response reconciles to the existing case", ok,
        "UNKNOWN then adopted, 1 root case",
        f"first={lost.slack_effect.journal_state}, posts={slack.posts}")

    # K04 restart after provider write
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status, FaultPlan().write_succeeds_response_lost("slack"))
    restarted = Journal(path)
    found_open = len(restarted.unfinished_effects(bundle.case_id))
    results = reconcile_unfinished_effects(restarted, slack_writer=slack, case_id=bundle.case_id)
    ok = (found_open >= 1
          and any(r.outcome is ReconciliationOutcome.ADOPTED for _, r in results)
          and restarted.unfinished_effects(bundle.case_id) == []
          and slack.posts == 1)
    add("K04", "restart adopts the existing resource", ok,
        "unfinished found, adopted, no duplicate",
        f"open={found_open}, adopted={[r.outcome.value for _, r in results]}, posts={slack.posts}")

    # K05 stored resource id avoids the history scan
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status)
    searches = {"n": 0}
    original = slack.find_case_by_marker
    slack.find_case_by_marker = lambda m, limit=200: (searches.__setitem__("n", searches["n"] + 1)
                                                      or original(m, limit))
    run(path, make_bundle(), slack, status)
    ok = searches["n"] == 0
    add("K05", "a known resource id is read directly", ok, "0 history scans",
        f"{searches['n']} scan(s)")

    # K06 resource id lost, marker recovery
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status)
    result = reconcile_slack_case(slack_writer=slack,
                                  marker=f"clauseci-case:{bundle.case_id}",
                                  known_resource_id=None, expected_values={})
    ok = result.outcome is ReconciliationOutcome.ADOPTED and "marker search" in result.detail
    add("K06", "marker recovery adopts the one existing case", ok, "ADOPTED by marker",
        f"{result.outcome.value}")

    # K07 duplicate marker ambiguity
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    marker = f"clauseci-case:{bundle.case_id}"
    slack.seed_case(f"one {marker}")
    slack.seed_case(f"two {marker}")
    receipt, _, _ = run(path, bundle, slack, status)
    ok = (slack.posts == 0 and receipt.execution_state is not ExecutionState.VERIFIED
          and receipt.slack_effect.reconciliation_outcome == ReconciliationOutcome.AMBIGUOUS.value)
    add("K07", "duplicate markers refuse a third case", ok, "no third, not VERIFIED",
        f"posts={slack.posts}, state={receipt.execution_state.value}")

    # K08 wrong slack payload
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    slack.tamper = lambda text: "an unrelated body"
    receipt, _, _ = run(path, None, slack, status)
    ok = receipt.slack_effect.matched is False and receipt.execution_state is not ExecutionState.VERIFIED
    add("K08", "a wrong Slack payload fails verification", ok, "not matched",
        f"matched={receipt.slack_effect.matched}")

    # K09 existing correct github status is adopted
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status)
    writes_after_first = len(status.writes)
    second, _, _ = run(db(), bundle, slack, status)
    ok = (len(status.writes) == writes_after_first
          and second.github_effect.action == "adopt_existing_status")
    add("K09", "an already correct status is adopted, not rewritten", ok,
        "no new status", f"{len(status.writes)} write(s), action={second.github_effect.action}")

    # K10 wrong github context is not adopted
    from clauseci.reconcile import reconcile_github_status
    status = FakeStatusWriter()
    status.statuses[("a" * 40, "Other / check")] = {
        "id": 1, "context": "Other / check", "state": "failure", "description": "x"}
    result = reconcile_github_status(status_writer=status, owner="o", repo="r", sha="a" * 40,
                                     context="ClauseCI / retention-compliance",
                                     intended_state="failure", intended_description="x")
    ok = result.outcome is ReconciliationOutcome.ABSENT
    add("K10", "a status in another context is not adopted", ok, "ABSENT", result.outcome.value)

    # K11 to K14 freshness
    for sid, name, point, state, expect_writes in (
        ("K11", "sha change before effects writes nothing",
         FaultPoint.FRESHNESS_BEFORE_EFFECTS, FreshnessState.SUPERSEDED_BEFORE_EXECUTION, 0),
        ("K13", "corpus change before effects writes nothing",
         FaultPoint.FRESHNESS_BEFORE_EFFECTS, FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION, 0),
    ):
        path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
        receipt, _, _ = run(path, None, slack, status,
                            FaultPlan().freshness_changes_at(point, state))
        ok = (receipt.execution_state is ExecutionState.SUPERSEDED
              and len(status.writes) == expect_writes and slack.posts == 0)
        add(sid, name, ok, "SUPERSEDED, zero writes",
            f"{receipt.execution_state.value}, {len(status.writes)} github, {slack.posts} slack")

    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    receipt, _, _ = run(path, None, slack, status, FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BETWEEN_EFFECTS, FreshnessState.SUPERSEDED_BEFORE_EXECUTION))
    ok = (receipt.execution_state is not ExecutionState.VERIFIED
          and len(status.writes) == 1 and receipt.github_effect.matched is True
          and slack.posts == 0 and receipt.superseded_after_effects is True)
    add("K12", "sha change after the first effect cannot produce a false VERIFIED", ok,
        "not VERIFIED, github effect preserved",
        f"{receipt.execution_state.value}, github matched={receipt.github_effect.matched}")

    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    receipt, _, _ = run(path, None, slack, status, FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BEFORE_SEAL, FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION))
    ok = (receipt.execution_state is not ExecutionState.VERIFIED
          and receipt.github_effect.matched and receipt.slack_effect.matched
          and receipt.superseded_after_effects is True)
    add("K14", "corpus change after effects preserves the audit and is not VERIFIED", ok,
        "not VERIFIED, both effects preserved", receipt.execution_state.value)

    # K15 forbidden transition
    refused = 0
    for current, target in ((EffectState.VERIFIED, EffectState.UNKNOWN),
                            (EffectState.FAILED, EffectState.VERIFIED),
                            (EffectState.PLANNED, EffectState.VERIFIED),
                            (EffectState.SUPERSEDED, EffectState.IN_FLIGHT)):
        try:
            check_transition(current, target)
        except IllegalTransition:
            refused += 1
    add("K15", "forbidden state transitions are refused", refused == 4, "4 refused",
        f"{refused} refused")

    # K16 deterministic effect key
    fields = dict(case_id="c", analysis_id="a", provider="slack", action_type="post",
                  target="t", intended_payload_digest="d")
    same = build_effect_key(**fields) == build_effect_key(**fields)
    differs = build_effect_key(**fields) != build_effect_key(**{**fields,
                                                               "intended_payload_digest": "d2"})
    add("K16", "effect keys are deterministic and payload sensitive", same and differs,
        "same in, same key; changed payload, changed key",
        f"same={same}, differs={differs}")

    # K17 same PR new sha
    case_a = build_case_id("o", "r", 1)
    case_b = build_case_id("o", "r", 1)
    common = dict(case_id=case_a, base_sha="b" * 40, corpus_digest="c" * 64,
                  policy_version="p", parser_version="q", semantic_model="m",
                  semantic_prompt_version="v", decision_policy_version="d")
    ids_differ = build_analysis_id(head_sha="1" * 40, **common) != \
        build_analysis_id(head_sha="2" * 40, **common)
    add("K17", "same pull request, new commit, same case, new analysis",
        case_a == case_b and ids_differ, "same case id, different analysis id",
        f"case_same={case_a == case_b}, analysis_differs={ids_differ}")

    # K18 unknown outcome is preserved and never triggers a blind create
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    receipt, journal, _ = run(path, bundle, slack, status,
                              FaultPlan().write_succeeds_response_lost("slack"))
    unknown = [e for e in journal.effects_for_case(bundle.case_id)
               if e.state is EffectState.UNKNOWN]
    run(path, bundle, slack, status)
    run(path, bundle, slack, status)
    ok = bool(unknown) and slack.posts == 1
    add("K18", "an UNKNOWN outcome never triggers a blind create", ok,
        "UNKNOWN recorded, still 1 post", f"unknown={len(unknown)}, posts={slack.posts}")

    # K19 human modified slack case
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run(path, bundle, slack, status)
    ts = list(slack.messages)[0]
    slack.messages[ts] += "\n\nEdited by a person."
    from tests.test_decision import HERO_CAPS
    tighter = {**HERO_CAPS, "acme-corp": {"application_logs": 25, "diagnostic_logs": 25,
                                          "audit_logs": 25}}
    receipt, _, _ = run(path, make_bundle(tighter), slack, status)
    ok = (receipt.slack_effect.action == "refused_foreign_content"
          and "Edited by a person." in slack.messages[ts])
    add("K19", "a human edited case is not silently overwritten", ok,
        "refused, edit preserved", receipt.slack_effect.action)

    # K20 receipt completeness
    path, slack, status = db(), FakeSlackWriter(), FakeStatusWriter()
    good, _, _ = run(path, None, slack, status)
    path2, slack2, status2 = db(), FakeSlackWriter(), FakeStatusWriter()
    bad, _, _ = run(path2, None, slack2, status2, FaultPlan().fail_before_call("slack"))
    ok = (good.execution_state is ExecutionState.VERIFIED
          and bad.execution_state is not ExecutionState.VERIFIED)
    add("K20", "VERIFIED requires every required effect", ok,
        "clean VERIFIED, failed effect not VERIFIED",
        f"clean={good.execution_state.value}, failed={bad.execution_state.value}")

    return out


# ═════════════════════════════════════════════════════════════ LIFECYCLE

def lifecycle_checks(tmp_root: Path) -> list[tuple]:
    """The Phase 7 business lifecycle, run locally against fake providers."""
    from tests.fakes import FakeSlackWriter, FakeStatusWriter
    from tests.test_lifecycle import corrected_bundle, execute, unsafe_bundle

    out = []

    def add(sid, name, ok, expected, actual, expected_release=None, actual_release=None):
        out.append((sid, name, ok, expected, actual,
                    "" if ok else "unexpected lifecycle outcome",
                    expected_release, actual_release))

    path = tmp_root / "lifecycle.sqlite3"
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    first, journal = execute(path, unsafe, slack, status)
    case_after_unsafe = journal.get_case(unsafe.case_id)

    add("LC01", "the unsafe commit conflicts and opens the case",
        first.decision is DecisionState.CONFLICT
        and case_after_unsafe.state == CaseState.OPEN.value
        and slack.posts == 1,
        "CONFLICT, case OPEN, one case",
        f"{first.decision.value}, {case_after_unsafe.state}, {slack.posts} post",
        expected_release="CONFLICT", actual_release=first.decision.value)

    corrected = corrected_bundle()
    second, journal = execute(path, corrected, slack, status)
    case_after = journal.get_case(unsafe.case_id)
    analyses = journal.analyses_for_case(unsafe.case_id)

    add("LC02", "the corrected commit is a new analysis of the same case",
        corrected.case_id == unsafe.case_id
        and corrected.analysis_id != unsafe.analysis_id
        and second.decision is DecisionState.PASS_SCOPED,
        "same case, new analysis, PASS_SCOPED",
        f"same_case={corrected.case_id == unsafe.case_id}, "
        f"new_analysis={corrected.analysis_id != unsafe.analysis_id}, "
        f"{second.decision.value}",
        expected_release="PASS_SCOPED", actual_release=second.decision.value)

    add("LC03", "the corrected run resolves the same case and verifies",
        second.execution_state is ExecutionState.VERIFIED
        and second.slack_effect.action == "resolve_case"
        and case_after.state == CaseState.RESOLVED.value
        and slack.posts == 1,
        "VERIFIED, resolve_case, RESOLVED, one root",
        f"{second.execution_state.value}, {second.slack_effect.action}, "
        f"{case_after.state}, {slack.posts} post")

    writes_before, updates_before = len(status.writes), slack.updates
    replay, journal = execute(path, corrected_bundle(), slack, status)
    add("LC04", "replaying the corrected head performs no new provider write",
        len(status.writes) == writes_before and slack.updates == updates_before
        and slack.posts == 1,
        "no new writes, one root case",
        f"github {len(status.writes)} (was {writes_before}), "
        f"slack updates {slack.updates} (was {updates_before})")

    decisions = {a["head_sha"]: a["decision"] for a in analyses}
    failures = [w for w in status.writes if w["state"] == "failure"]
    successes = [w for w in status.writes if w["state"] == "success"]
    add("LC05", "the old commit keeps its failure and the new one its success",
        len(analyses) == 2 and "CONFLICT" in decisions.values()
        and "PASS_SCOPED" in decisions.values()
        and failures and failures[0]["sha"] == unsafe.snapshot.head_sha
        and successes and successes[0]["sha"] == corrected.snapshot.head_sha,
        "two analyses, old failure, new success",
        f"{len(analyses)} analyses, failure on {failures[0]['sha'][:8] if failures else None}, "
        f"success on {successes[0]['sha'][:8] if successes else None}")

    return out
