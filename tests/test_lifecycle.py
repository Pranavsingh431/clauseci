"""
Phase 7 tests: the correction lifecycle.

One case, two commits. The unsafe commit keeps its failing result forever. The
corrected commit earns its own. The same Slack case closes rather than a second
one appearing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clauseci.correction import CorrectionError, build_correction
from clauseci.domain.case_message import render_resolved_case, resolved_expected_fields
from clauseci.domain.decision import DecisionState
from clauseci.domain.execution import (
    ExecutionState,
    SlackAction,
    build_execution_plan,
    slack_action_for,
)
from clauseci.journal import CaseState, Journal
from clauseci.registry import load_registry
from tests.fakes import FakeGitHubReader, FakeSlackWriter, FakeStatusWriter
from tests.test_decision import BASE_TEXT, HEAD_TEXT, HERO_CAPS, build
from tests.test_execution import make_bundle, make_workflow

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CORRECTED_TEXT = (FIXTURES / "retention_correction.yaml").read_text()


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "lifecycle.sqlite3"


def unsafe_bundle():
    return make_bundle()


CORRECTED_SHA = "c" * 40


def corrected_bundle():
    """
    The same case at a genuinely different commit.

    The snapshot carries a new head SHA, which is what makes this a second
    analysis of one case rather than a re-run of the same one.
    """
    from clauseci.domain.execution import build_analysis_id

    bundle = make_bundle(head_text=CORRECTED_TEXT)
    bundle.snapshot = bundle.snapshot.model_copy(update={"head_sha": CORRECTED_SHA})
    bundle.decision = bundle.decision.model_copy(update={
        "binding": bundle.decision.binding.model_copy(update={"head_sha": CORRECTED_SHA}),
        "coverage": bundle.decision.coverage.model_copy(update={"head_sha": CORRECTED_SHA}),
    })
    bundle.analysis_id = build_analysis_id(
        case_id=bundle.case_id, base_sha=bundle.snapshot.base_sha,
        head_sha=CORRECTED_SHA, corpus_digest=bundle.snapshot.corpus_digest,
        policy_version=bundle.snapshot.policy_version,
        parser_version=bundle.snapshot.parser_version,
        semantic_model="anthropic/claude-sonnet-4.5",
        semantic_prompt_version="retention-extract-1.0.0",
        decision_policy_version="scoped-retention-decision-1.0.0",
    )
    return bundle


def reader_for(bundle):
    """A fake GitHub whose reported head matches the bundle being executed."""
    reader = FakeGitHubReader()
    head = bundle.snapshot.head_sha
    base = bundle.snapshot.base_sha
    reader.get_pull_request = lambda owner, repo, number: {
        "number": number, "title": "t", "html_url": "u",
        "base": {"sha": base, "ref": "main"},
        "head": {"sha": head, "ref": "feature/increase-log-retention"},
    }
    return reader


def execute(db, bundle, slack, status, faults=None):
    journal = Journal(db)
    workflow = make_workflow(bundle, slack_writer=slack, status_writer=status,
                             journal=journal, faults=faults,
                             github_reader=reader_for(bundle))
    return workflow.execute(bundle, workflow.build_plan(bundle)), journal


# --------------------------------------------------------- LFC01 to LFC06

def test_lfc01_an_unsafe_analysis_opens_the_case(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    receipt, journal = execute(db, unsafe_bundle(), slack, status)
    assert receipt.decision is DecisionState.CONFLICT
    case = journal.get_case(receipt.case_id)
    assert case.state == CaseState.OPEN.value
    assert case.is_open is True


def test_lfc02_the_corrected_commit_keeps_the_same_case_id():
    assert unsafe_bundle().case_id == corrected_bundle().case_id


def test_lfc03_the_corrected_commit_gets_a_different_analysis_id():
    assert unsafe_bundle().analysis_id != corrected_bundle().analysis_id


def test_lfc04_the_corrected_configuration_evaluates_pass_scoped():
    decision = build(HERO_CAPS, head_text=CORRECTED_TEXT)
    assert decision.actual_head_state is DecisionState.PASS_SCOPED


def test_lfc05_the_unsafe_analysis_stays_conflict_after_the_correction(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    journal = Journal(db)
    decisions = {row["head_sha"]: row["decision"]
                 for row in journal.analyses_for_case(unsafe.case_id)}
    assert "CONFLICT" in decisions.values()
    assert "PASS_SCOPED" in decisions.values()


def test_lfc06_the_old_sealed_receipt_is_never_rewritten(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    first, journal = execute(db, unsafe, slack, status)
    before = json.dumps(first.model_dump(mode="json"), sort_keys=True)

    execute(db, corrected_bundle(), slack, status)

    rows = Journal(db)._connection.execute(
        "SELECT receipt_json FROM receipts WHERE case_id = ? ORDER BY created_at",
        (unsafe.case_id,)).fetchall()
    assert len(rows) >= 2, "a second receipt is created, not a rewrite"
    stored_first = json.loads(rows[0]["receipt_json"])
    assert stored_first["decision"] == "CONFLICT"
    assert stored_first["head_sha"] == json.loads(before)["head_sha"]


# --------------------------------------------------------- LFC07 to LFC12

def test_lfc07_a_pass_with_no_prior_case_touches_no_slack():
    assert slack_action_for(DecisionState.PASS_SCOPED, case_is_open=False) is SlackAction.NONE


def test_lfc08_a_pass_with_an_open_case_must_resolve_it():
    assert slack_action_for(DecisionState.PASS_SCOPED, case_is_open=True) is SlackAction.RESOLVE_CASE


def test_no_supported_change_never_silently_resolves_an_open_case():
    assert slack_action_for(DecisionState.NO_SUPPORTED_CHANGE, case_is_open=True) is SlackAction.NONE


def test_a_clean_pass_on_a_fresh_case_writes_no_slack(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    receipt, journal = execute(db, corrected_bundle(), slack, status)
    assert receipt.decision is DecisionState.PASS_SCOPED
    assert slack.posts == 0 and slack.updates == 0
    assert receipt.slack_effect is None


def test_lfc09_the_resolution_targets_the_exact_existing_resource(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    original_ts = list(slack.messages)[0]

    receipt, journal = execute(db, corrected_bundle(), slack, status)
    assert receipt.slack_effect.action == "resolve_case"
    assert receipt.slack_effect.provider_resource_id == f"{slack.channel_id}/{original_ts}"


def test_lfc10_the_resolution_creates_no_second_root_case(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    assert slack.posts == 1
    assert len(slack.find_case_by_marker(f"clauseci-case:{unsafe.case_id}")) == 1


def test_lfc11_the_resolved_message_carries_both_commits(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    text = list(slack.messages.values())[0]
    assert "RESOLVED" in text
    assert unsafe.snapshot.head_sha in text
    assert "previous_sha:" in text
    assert "Previous commit" in text and "Current commit" in text


def test_lfc12_the_resolved_message_credits_a_developer_not_the_agent(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    execute(db, corrected_bundle(), slack, status)

    text = list(slack.messages.values())[0]
    assert "Resolved by a developer commit" in text
    assert "It did not change the code." in text
    for banned in ("auto-remediated", "fixed automatically", "ClauseCI changed the code",
                   "ClauseCI applied", "ClauseCI committed", "ClauseCI pushed"):
        assert banned not in text


# --------------------------------------------------------- LFC13 to LFC18

def test_lfc13_success_is_written_to_the_corrected_commit(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    receipt, _ = execute(db, corrected_bundle(), slack, status)
    assert receipt.github_effect.normalized_observed_payload["state"] == "success"


def test_lfc14_the_old_commit_keeps_its_failure_history(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    failures = [w for w in status.writes if w["state"] == "failure"]
    assert failures and failures[0]["sha"] == unsafe.snapshot.head_sha
    successes = [w for w in status.writes if w["state"] == "success"]
    assert successes
    assert all(w["sha"] == unsafe.snapshot.head_sha for w in failures)


def test_lfc15_a_new_success_cannot_change_the_old_analysis(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    first, _ = execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    journal = Journal(db)
    rows = journal.analyses_for_case(unsafe.case_id)
    old = [r for r in rows if r["head_sha"] == unsafe.snapshot.head_sha][0]
    assert old["decision"] == "CONFLICT", "the old analysis is immutable"
    assert first.decision is DecisionState.CONFLICT


def test_lfc16_the_corrected_run_creates_its_own_effect_keys(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    first, _ = execute(db, unsafe, slack, status)
    second, journal = execute(db, corrected_bundle(), slack, status)

    assert first.github_effect.effect_key != second.github_effect.effect_key
    assert first.slack_effect.effect_key != second.slack_effect.effect_key
    keys = {e.effect_key for e in journal.effects_for_case(unsafe.case_id)}
    assert len(keys) == 4, "old effects are preserved alongside the new ones"


def test_lfc17_one_case_holds_both_analyses(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    journal = Journal(db)
    assert len(journal.analyses_for_case(unsafe.case_id)) == 2
    assert len({c for c in [unsafe.case_id, corrected_bundle().case_id]}) == 1


def test_lfc18_the_case_closes_only_after_the_closure_verifies(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    assert Journal(db).get_case(unsafe.case_id).state == CaseState.OPEN.value

    receipt, journal = execute(db, corrected_bundle(), slack, status)
    case = journal.get_case(unsafe.case_id)
    assert receipt.slack_effect.matched is True
    assert case.state == CaseState.RESOLVED.value
    assert case.resolved_head_sha == CORRECTED_SHA
    assert case.resolved_at


def test_lfc18_a_failed_closure_leaves_the_case_open(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)

    slack.tamper = lambda text: "the closure body was lost"
    receipt, journal = execute(db, corrected_bundle(), slack, status)
    assert receipt.slack_effect.matched is False
    assert journal.get_case(unsafe.case_id).state == CaseState.OPEN.value


# --------------------------------------------------------- LFC19 to LFC23

def test_lfc19_github_success_with_a_failed_closure_is_not_verified(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    slack.tamper = lambda text: "closure lost"
    receipt, _ = execute(db, corrected_bundle(), slack, status)

    assert receipt.github_effect.matched is True
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_lfc20_a_closure_naming_the_wrong_commit_fails_verification(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    corrected = corrected_bundle()
    execute(db, unsafe_bundle(), slack, status)
    slack.tamper = lambda text: text.replace(corrected.snapshot.head_sha, "9" * 40)
    receipt, _ = execute(db, corrected, slack, status)

    assert receipt.slack_effect.matched is False
    assert any("head_sha" in m for m in receipt.slack_effect.mismatches)
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_lfc21_a_human_edited_open_case_is_not_overwritten_by_a_resolution(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)

    ts = list(slack.messages)[0]
    slack.messages[ts] += "\n\nEdited by a person: keeping this open deliberately."

    receipt, journal = execute(db, corrected_bundle(), slack, status)
    assert receipt.slack_effect.action == "refused_foreign_content"
    assert receipt.execution_state is not ExecutionState.VERIFIED
    assert journal.get_case(unsafe.case_id).state == CaseState.OPEN.value
    assert "Edited by a person" in slack.messages[ts]


def test_lfc22_replaying_the_corrected_head_creates_no_second_root(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)
    execute(db, corrected_bundle(), slack, status)

    assert slack.posts == 1
    assert len(slack.find_case_by_marker(f"clauseci-case:{unsafe.case_id}")) == 1


def test_lfc23_replaying_performs_no_unnecessary_provider_write(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    execute(db, corrected_bundle(), slack, status)
    writes_before, updates_before = len(status.writes), slack.updates

    replay, journal = execute(db, corrected_bundle(), slack, status)
    assert len(status.writes) == writes_before, "no new status for an identical intent"
    assert slack.updates == updates_before, "no rewrite of an already resolved case"
    # the case is already RESOLVED, so a pass has nothing to close and Slack is
    # not touched at all
    assert replay.slack_effect is None
    assert journal.get_case(replay.case_id).state == CaseState.RESOLVED.value
    assert replay.github_effect.action == "already_verified"


# --------------------------------------------------------- LFC24 to LFC28

def test_lfc24_the_lifecycle_inspector_shows_both_analyses(db, capsys):
    from clauseci.state import inspect as inspect_case
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    unsafe = unsafe_bundle()
    execute(db, unsafe, slack, status)
    execute(db, corrected_bundle(), slack, status)

    inspect_case(Journal(db), unsafe.case_id)
    output = capsys.readouterr().out
    assert "ANALYSIS 1" in output and "ANALYSIS 2" in output
    assert "CONFLICT" in output and "PASS_SCOPED" in output
    assert "RESOLVED" in output
    assert unsafe.snapshot.head_sha in output


def test_lfc25_the_correction_recomputes_to_the_candidate():
    decision = build(HERO_CAPS)
    correction = build_correction(decision, load_registry())
    assert correction["candidate_id"] == "C"
    assert correction["applied"] is False
    values = correction["effective_values_after"]
    assert values["acme-corp.application_logs"] == 30
    assert values["globex.application_logs"] == 90
    assert values["acme-labs.application_logs"] == 90


def test_lfc25_a_correction_that_does_not_recompute_is_refused():
    decision = build(HERO_CAPS)
    candidate = decision.preferred_candidate
    broken = candidate.model_copy(update={"proposed_yaml": BASE_TEXT})
    others = tuple(c if c.candidate_id != "C" else broken for c in decision.candidates)
    with pytest.raises(CorrectionError):
        build_correction(decision.model_copy(update={"candidates": others}), load_registry())


def test_lfc26_the_runtime_contains_no_code_that_pushes_a_commit():
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        for banned in ("git push", "git commit", "subprocess", "os.system",
                       "GitPython", "import git\n"):
            assert banned not in body, f"{path.name} could push code: {banned}"


def test_lfc27_no_merge_operation_exists_in_the_write_adapters():
    for name in ("github_write.py", "slack_write.py"):
        body = (ROOT / "clauseci" / "adapters" / name).read_text()
        for banned in ("/merge", "merge_pull_request", "def merge", "PUT /repos",
                       "/pulls/", "/git/refs"):
            assert banned not in body, f"{name} contains {banned}"


def test_lfc28_resolution_stays_scoped_and_claims_no_legal_compliance(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    execute(db, corrected_bundle(), slack, status)

    text = list(slack.messages.values())[0]
    assert "not a statement of legal compliance" in text
    assert "represented log retention configuration" in text
    for banned in ("fully compliant", "legally compliant", "all obligations met",
                   "guaranteed", "certified"):
        assert banned not in text


def test_the_resolved_message_does_not_invent_a_cap_for_an_unrepresented_category(db):
    slack, status = FakeSlackWriter(), FakeStatusWriter()
    execute(db, unsafe_bundle(), slack, status)
    execute(db, corrected_bundle(), slack, status)
    text = list(slack.messages.values())[0]
    assert "acme-labs.audit_logs" in text
    assert "No limit is inferred" in text
