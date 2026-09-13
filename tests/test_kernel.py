"""
Phase 6 tests: durable journal, reconciliation and failure recovery.

These use a real SQLite file so a "restart" can be simulated by opening a fresh
Journal against the same database, which is what a new process would do.
"""

from __future__ import annotations

import pytest

from clauseci.domain.effects import (
    ALLOWED_TRANSITIONS,
    EffectState,
    IllegalTransition,
    build_effect_key,
    check_transition,
)
from clauseci.domain.execution import ExecutionState, FreshnessState, build_analysis_id, build_case_id
from clauseci.faults import FaultPlan, FaultPoint
from clauseci.journal import Journal, LockNotAcquired, SchemaMismatch
from clauseci.reconcile import ReconciliationOutcome, reconcile_unfinished_effects
from clauseci.registry import load_registry
from tests.fakes import FakeDriveReader, FakeGitHubReader, FakeSlackWriter, FakeStatusWriter
from tests.test_execution import DEFAULT, make_bundle, make_workflow


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "clauseci.sqlite3"


def run_once(db, *, slack=None, status=None, faults=None, bundle=None, drive=None,
             github_reader=None):
    """One execution, as a fresh process would do it."""
    bundle = bundle or make_bundle()
    journal = Journal(db)
    workflow = make_workflow(
        bundle, slack_writer=slack or FakeSlackWriter(),
        status_writer=status or FakeStatusWriter(),
        journal=journal, faults=faults, drive=drive, github_reader=github_reader,
    )
    receipt = workflow.execute(bundle)
    return receipt, journal


# ------------------------------------------------------------- K01 and K02

def test_k01_invoking_the_same_execution_twice_leaves_one_slack_case(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    first, journal = run_once(db, slack=slack, status=status, bundle=bundle)
    second, _ = run_once(db, slack=slack, status=status, bundle=bundle)

    assert first.execution_state is ExecutionState.VERIFIED
    assert slack.posts == 1
    assert len(slack.find_case_by_marker(f"clauseci-case:{bundle.case_id}")) == 1
    assert second.slack_effect.action == "already_verified"


def test_k02_a_second_local_execution_cannot_double_write(db):
    bundle = make_bundle()
    holder = Journal(db)
    assert holder.acquire_lock(bundle.case_id, "process-one") is True

    slack, status = FakeSlackWriter(), FakeStatusWriter()
    receipt, _ = run_once(db, slack=slack, status=status, bundle=bundle)

    assert receipt.execution_state is ExecutionState.FAILED
    assert slack.posts == 0, "the second process must not write"
    assert status.writes == [], "the second process must not write"
    assert any("already owns case" in note for note in receipt.notes)


def test_k02_the_lock_is_released_so_the_next_run_proceeds(db):
    bundle = make_bundle()
    slack = FakeSlackWriter()
    run_once(db, slack=slack, bundle=bundle)
    journal = Journal(db)
    assert journal.acquire_lock(bundle.case_id, "someone-else") is True


def test_a_stale_lock_is_taken_over(db):
    journal = Journal(db)
    journal.acquire_lock("case-x", "dead-process", ttl_seconds=-1)
    assert journal.acquire_lock("case-x", "live-process") is True


# --------------------------------------------------------------------- K03

def test_k03_slack_write_succeeds_but_the_response_is_lost(db):
    """The provider accepted it. Only this process lost the answer."""
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    faults = FaultPlan().write_succeeds_response_lost("slack")

    lost, journal = run_once(db, slack=slack, status=status, faults=faults, bundle=bundle)

    assert lost.execution_state is ExecutionState.UNKNOWN
    assert lost.slack_effect.journal_state == "UNKNOWN"
    assert slack.posts == 1, "the write really happened"

    # the retry must reconcile, not post again
    recovered, journal2 = run_once(db, slack=slack, status=status, bundle=bundle)
    assert slack.posts == 1, "no second root case may be created"
    assert len(slack.find_case_by_marker(f"clauseci-case:{bundle.case_id}")) == 1
    assert any("recovered slack effect" in note for note in recovered.notes)


def test_k03_recovery_adopts_the_real_resource_id(db):
    slack = FakeSlackWriter()
    bundle = make_bundle()
    faults = FaultPlan().write_succeeds_response_lost("slack")
    run_once(db, slack=slack, faults=faults, bundle=bundle)

    journal = Journal(db)
    reconcile_unfinished_effects(journal, slack_writer=slack, case_id=bundle.case_id)
    case = journal.get_case(bundle.case_id)
    real_ts = list(slack.messages)[0]
    assert case.slack_resource_id == f"{slack.channel_id}/{real_ts}"


# --------------------------------------------------------------------- K04

def test_k04_restart_after_a_write_discovers_and_reconciles_the_effect(db):
    """A fresh Journal against the same file is what a new process would open."""
    slack = FakeSlackWriter()
    bundle = make_bundle()
    faults = FaultPlan().write_succeeds_response_lost("slack")
    run_once(db, slack=slack, faults=faults, bundle=bundle)

    restarted = Journal(db)  # simulates a new process
    open_before = restarted.unfinished_effects(bundle.case_id)
    assert open_before, "the restart must find the unfinished effect"

    results = reconcile_unfinished_effects(restarted, slack_writer=slack,
                                           case_id=bundle.case_id)
    assert [r.outcome for _, r in results] == [ReconciliationOutcome.ADOPTED]
    assert restarted.unfinished_effects(bundle.case_id) == []
    assert slack.posts == 1


# ------------------------------------------------------------- K05 and K06

def test_k05_a_known_resource_id_is_read_directly_without_a_history_scan(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run_once(db, slack=slack, status=status, bundle=bundle)

    searches = {"count": 0}
    original = slack.find_case_by_marker

    def counted(marker, limit=200):
        searches["count"] += 1
        return original(marker, limit)

    slack.find_case_by_marker = counted

    changed = make_bundle()  # same case, fresh analysis identity path
    run_once(db, slack=slack, status=status, bundle=changed)
    assert searches["count"] == 0, "the stored resource id must be used directly"


def test_k06_a_lost_resource_id_is_recovered_through_the_marker(db):
    slack = FakeSlackWriter()
    bundle = make_bundle()
    run_once(db, slack=slack, bundle=bundle)

    journal = Journal(db)
    journal.set_case_slack_resource(bundle.case_id, None, None)   # the id is gone
    effect = [e for e in journal.effects_for_case(bundle.case_id) if e.provider == "slack"][0]
    journal.transition_effect(effect.effect_key, EffectState.UNKNOWN) if False else None

    from clauseci.reconcile import reconcile_slack_case
    result = reconcile_slack_case(
        slack_writer=slack, marker=f"clauseci-case:{bundle.case_id}",
        known_resource_id=None, expected_values={},
    )
    assert result.outcome is ReconciliationOutcome.ADOPTED
    assert "marker search" in result.detail
    assert result.provider_resource_id == f"{slack.channel_id}/{list(slack.messages)[0]}"


# ------------------------------------------------------------- K07 and K08

def test_k07_two_cases_with_the_same_marker_refuse_a_third(db):
    slack = FakeSlackWriter()
    bundle = make_bundle()
    marker = f"clauseci-case:{bundle.case_id}"
    slack.seed_case(f"old {marker}")
    slack.seed_case(f"duplicate {marker}")

    receipt, _ = run_once(db, slack=slack, bundle=bundle)
    assert slack.posts == 0
    assert receipt.execution_state is not ExecutionState.VERIFIED
    assert receipt.slack_effect.reconciliation_outcome == ReconciliationOutcome.AMBIGUOUS.value


def test_k08_a_wrong_slack_read_back_fails_verification(db):
    slack = FakeSlackWriter()
    faults = FaultPlan().wrong_readback(
        "slack", lambda observed: {**observed, "text": "something else entirely"})
    receipt, _ = run_once(db, slack=slack, faults=faults)
    assert receipt.slack_effect.matched is False
    assert receipt.execution_state is not ExecutionState.VERIFIED


# ------------------------------------------------------------- K09 and K10

def test_k09_an_already_correct_github_status_is_adopted_not_rewritten(tmp_path):
    status, slack = FakeStatusWriter(), FakeSlackWriter()
    bundle = make_bundle()
    run_once(tmp_path / "first.sqlite3", status=status, slack=slack, bundle=bundle)
    assert len(status.writes) == 1

    # a completely fresh journal, as a machine with no local history would have,
    # against provider state that is already correct
    second, _ = run_once(tmp_path / "second.sqlite3", status=status, slack=slack,
                         bundle=bundle)
    assert len(status.writes) == 1, "an identical latest status must not be appended"
    assert second.github_effect.action == "adopt_existing_status"
    assert second.github_effect.reconciliation_outcome == ReconciliationOutcome.ADOPTED.value


def test_k10_a_status_in_the_wrong_context_is_not_adopted(db):
    from clauseci.reconcile import reconcile_github_status
    status = FakeStatusWriter()
    status.statuses[("a" * 40, "Other / check")] = {
        "id": 1, "context": "Other / check", "state": "failure", "description": "x"}
    result = reconcile_github_status(
        status_writer=status, owner="o", repo="r", sha="a" * 40,
        context="ClauseCI / retention-compliance", intended_state="failure",
        intended_description="x")
    assert result.outcome is ReconciliationOutcome.ABSENT


# --------------------------------------------------------------- K11 to K14

def test_k11_a_sha_change_before_any_effect_writes_nothing(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    faults = FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BEFORE_EFFECTS, FreshnessState.SUPERSEDED_BEFORE_EXECUTION)
    receipt, _ = run_once(db, slack=slack, status=status, faults=faults)
    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert status.writes == [] and slack.posts == 0


def test_k12_a_sha_change_after_github_cannot_produce_a_false_verified(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    faults = FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BETWEEN_EFFECTS, FreshnessState.SUPERSEDED_BEFORE_EXECUTION)
    receipt, journal = run_once(db, slack=slack, status=status, faults=faults)

    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert receipt.execution_state is not ExecutionState.VERIFIED
    assert len(status.writes) == 1, "the GitHub effect really happened"
    assert receipt.github_effect.matched is True, "and stays in the audit record"
    assert slack.posts == 0, "the stale Slack case must not be opened"
    assert receipt.superseded_after_effects is True


def test_k13_a_corpus_change_before_any_effect_writes_nothing(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    faults = FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BEFORE_EFFECTS,
        FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION)
    receipt, _ = run_once(db, slack=slack, status=status, faults=faults)
    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert status.writes == [] and slack.posts == 0


def test_k14_a_corpus_change_after_effects_preserves_them_but_is_not_verified(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    faults = FaultPlan().freshness_changes_at(
        FaultPoint.FRESHNESS_BEFORE_SEAL,
        FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION)
    receipt, _ = run_once(db, slack=slack, status=status, faults=faults)

    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert receipt.github_effect.matched is True
    assert receipt.slack_effect.matched is True
    assert receipt.superseded_after_effects is True
    assert receipt.freshness_at_seal is FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION


# --------------------------------------------------------------- K15 to K19

@pytest.mark.parametrize("current,target", [
    (EffectState.PLANNED, EffectState.IN_FLIGHT),
    (EffectState.IN_FLIGHT, EffectState.VERIFIED),
    (EffectState.IN_FLIGHT, EffectState.UNKNOWN),
    (EffectState.IN_FLIGHT, EffectState.FAILED),
    (EffectState.UNKNOWN, EffectState.VERIFIED),
    (EffectState.UNKNOWN, EffectState.FAILED),
    (EffectState.PLANNED, EffectState.SUPERSEDED),
])
def test_allowed_transitions_are_accepted(current, target):
    check_transition(current, target)


@pytest.mark.parametrize("current,target", [
    (EffectState.VERIFIED, EffectState.UNKNOWN),
    (EffectState.VERIFIED, EffectState.PLANNED),
    (EffectState.FAILED, EffectState.VERIFIED),
    (EffectState.SUPERSEDED, EffectState.IN_FLIGHT),
    (EffectState.PLANNED, EffectState.VERIFIED),
    (EffectState.UNKNOWN, EffectState.IN_FLIGHT),
])
def test_k15_forbidden_transitions_are_rejected(current, target):
    with pytest.raises(IllegalTransition):
        check_transition(current, target)


def test_k15_leaving_in_flight_for_superseded_needs_independent_evidence():
    with pytest.raises(IllegalTransition):
        check_transition(EffectState.IN_FLIGHT, EffectState.SUPERSEDED)
    check_transition(EffectState.IN_FLIGHT, EffectState.SUPERSEDED, outcome_known=True)


def test_k15_the_journal_refuses_an_illegal_transition(db):
    journal = Journal(db)
    journal.upsert_case(case_id="c", namespace="n", repository_full_name="o/r",
                        repository_id=1, pr_number=1)
    journal.record_analysis(
        analysis_id="a", case_id="c", base_sha="b", head_sha="h", corpus_digest="d",
        snapshot_schema_version="1", parser_version="1", policy_version="1",
        semantic_model="m", semantic_prompt_version="p", semantic_schema_version="s",
        decision_policy_version="dp", decision="CONFLICT")
    journal.plan_effect(effect_key="k", case_id="c", analysis_id="a", provider="slack",
                        action_type="post", target="t", intended_payload={},
                        intended_payload_digest="d")
    journal.transition_effect("k", EffectState.IN_FLIGHT)
    journal.transition_effect("k", EffectState.VERIFIED)
    with pytest.raises(IllegalTransition):
        journal.transition_effect("k", EffectState.UNKNOWN)


def test_k16_identical_inputs_produce_the_same_effect_key():
    fields = dict(case_id="c", analysis_id="a", provider="slack",
                  action_type="post_case", target="t", intended_payload_digest="d")
    assert build_effect_key(**fields) == build_effect_key(**fields)


def test_k17_a_changed_payload_produces_a_different_effect_key():
    fields = dict(case_id="c", analysis_id="a", provider="slack",
                  action_type="post_case", target="t", intended_payload_digest="d")
    assert build_effect_key(**fields) != build_effect_key(**{**fields,
                                                            "intended_payload_digest": "d2"})
    assert build_effect_key(**fields) != build_effect_key(**{**fields, "analysis_id": "a2"})


def test_k18_same_pr_different_sha_keeps_the_same_case_id():
    assert build_case_id("o", "r", 1) == build_case_id("o", "r", 1)


def test_k19_same_pr_different_sha_gives_a_different_analysis_id():
    common = dict(case_id=build_case_id("o", "r", 1), base_sha="b" * 40,
                  corpus_digest="c" * 64, policy_version="p", parser_version="q",
                  semantic_model="m", semantic_prompt_version="v",
                  decision_policy_version="d")
    first = build_analysis_id(head_sha="1" * 40, **common)
    second = build_analysis_id(head_sha="2" * 40, **common)
    assert first != second


def test_one_case_holds_many_analyses(db):
    journal = Journal(db)
    case_id = build_case_id("o", "r", 1)
    journal.upsert_case(case_id=case_id, namespace="n", repository_full_name="o/r",
                        repository_id=1, pr_number=1)
    for head in ("1" * 40, "2" * 40):
        journal.record_analysis(
            analysis_id=f"an-{head[:6]}", case_id=case_id, base_sha="b" * 40,
            head_sha=head, corpus_digest="c" * 64, snapshot_schema_version="1",
            parser_version="1", policy_version="1", semantic_model="m",
            semantic_prompt_version="p", semantic_schema_version="s",
            decision_policy_version="dp", decision="CONFLICT")
    analyses = journal.analyses_for_case(case_id)
    assert len(analyses) == 2
    assert {a["head_sha"] for a in analyses} == {"1" * 40, "2" * 40}


# --------------------------------------------------------------- K20 to K25

def test_k20_an_unestablished_outcome_stays_unknown(db):
    slack = FakeSlackWriter()
    faults = FaultPlan().write_succeeds_response_lost("slack")
    receipt, journal = run_once(db, slack=slack, faults=faults)
    effect = [e for e in journal.effects_for_case(receipt.case_id)
              if e.provider == "slack"][0]
    assert effect.state is EffectState.UNKNOWN
    assert receipt.execution_state is ExecutionState.UNKNOWN


def test_k21_an_unknown_effect_never_triggers_a_blind_create(db):
    slack = FakeSlackWriter()
    bundle = make_bundle()
    faults = FaultPlan().write_succeeds_response_lost("slack")
    run_once(db, slack=slack, faults=faults, bundle=bundle)
    assert slack.posts == 1

    run_once(db, slack=slack, bundle=bundle)          # retry
    run_once(db, slack=slack, bundle=bundle)          # and again
    assert slack.posts == 1, "an UNKNOWN outcome must be reconciled, never re-created"


def test_k22_a_human_edited_case_is_not_silently_overwritten(db):
    """
    The protection only matters when ClauseCI would actually write. A repeat of
    an identical intent writes nothing at all, so this uses a changed intent.
    """
    from tests.test_decision import HERO_CAPS
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    bundle = make_bundle()
    run_once(db, slack=slack, status=status, bundle=bundle)

    ts = list(slack.messages)[0]
    slack.messages[ts] = slack.messages[ts] + "\n\nEdited by a person: we accept this risk."

    # a different represented limit changes the case text, so a write is intended
    tighter = {**HERO_CAPS, "acme-corp": {"application_logs": 25, "diagnostic_logs": 25,
                                          "audit_logs": 25}}
    changed = make_bundle(tighter)
    receipt, _ = run_once(db, slack=slack, status=status, bundle=changed)
    assert receipt.slack_effect.action == "refused_foreign_content"
    assert "refusing to overwrite" in receipt.slack_effect.error
    assert receipt.execution_state is not ExecutionState.VERIFIED
    assert "Edited by a person" in slack.messages[ts], "the edit must survive"


def test_k23_a_receipt_cannot_be_verified_while_an_effect_is_unknown(db):
    slack = FakeSlackWriter()
    faults = FaultPlan().write_succeeds_response_lost("slack")
    receipt, _ = run_once(db, slack=slack, faults=faults)
    assert receipt.slack_effect.journal_state == "UNKNOWN"
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_k24_a_receipt_cannot_be_verified_while_an_effect_failed(db):
    slack = FakeSlackWriter()
    faults = FaultPlan().fail_before_call("slack")
    receipt, _ = run_once(db, slack=slack, faults=faults)
    assert receipt.slack_effect.journal_state == "FAILED"
    assert slack.posts == 0
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_k25_all_reconciled_effects_may_produce_verified(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    receipt, journal = run_once(db, slack=slack, status=status)
    assert receipt.execution_state is ExecutionState.VERIFIED
    effects = journal.effects_for_case(receipt.case_id)
    assert len(effects) == 2
    assert all(e.state is EffectState.VERIFIED for e in effects)
    assert all(e.provider_resource_id for e in effects)


# ------------------------------------------------------- journal integrity

def test_intent_is_persisted_before_the_provider_is_called(db):
    """The write must never happen before the intent is on disk."""
    slack = FakeSlackWriter()
    seen: list[int] = []
    original = slack.post_case

    def observing(text):
        seen.append(len(Journal(db).effects_for_case(make_bundle().case_id)))
        return original(text)

    slack.post_case = observing
    run_once(db, slack=slack)
    assert seen and seen[0] >= 1, "the effect row must exist before the provider call"


def test_the_journal_refuses_a_foreign_schema_version(db):
    journal = Journal(db)
    journal._connection.execute(
        "UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    journal.close()
    with pytest.raises(SchemaMismatch):
        Journal(db)


def test_the_journal_stores_no_credential(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    receipt, journal = run_once(db, slack=slack, status=status)
    rows = journal._connection.execute(
        "SELECT intended_payload_json FROM effects").fetchall()
    body = " ".join(row[0] for row in rows).lower()
    for banned in ("xoxb-", "github_pat", "sk-or-", "authorization", "bearer", "token"):
        assert banned not in body


def test_the_database_path_is_git_ignored():
    """
    Checks the real runtime location, not the temporary one the test session
    substitutes, so the guard cannot hide a genuinely tracked database.
    """
    import subprocess
    from clauseci.settings import ROOT
    for candidate in ("runs/state/clauseci.sqlite3", "runs/state/clauseci.sqlite3-wal"):
        result = subprocess.run(["git", "check-ignore", "-q", candidate],
                                cwd=ROOT, capture_output=True)
        assert result.returncode == 0, f"{candidate} must be git ignored"


def test_no_sqlite_database_is_tracked_anywhere():
    import subprocess
    from clauseci.settings import ROOT
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True).stdout.splitlines()
    assert not [f for f in tracked if f.endswith((".sqlite3", ".db", ".sqlite"))]
