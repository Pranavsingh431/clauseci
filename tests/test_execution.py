"""
Phase 5 tests: the bounded execution layer.

These run offline against fake providers. A live run is separate, and its
evidence is in the receipt it produces.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clauseci.domain.case_message import expected_fields, render_case
from clauseci.domain.decision import DecisionState
from clauseci.domain.execution import (
    ExecutionState,
    FreshnessState,
    GithubStatusState,
    SlackAction,
    VerificationState,
    build_analysis_id,
    build_case_id,
    build_execution_plan,
    case_marker,
)
from clauseci.registry import load_registry
from clauseci.versions import RETENTION_STATUS_CONTEXT, SMOKE_TEST_STATUS_CONTEXT
from clauseci.workflow import AnalysisBundle, Workflow
from tests.fakes import (
    FakeDriveReader,
    FakeGitHubReader,
    FakeSlackWriter,
    FakeStatusWriter,
    local_snapshot,
    local_text_provider,
)
from tests.test_decision import BASE_TEXT, HEAD_TEXT, HERO_CAPS, analysis_from, build

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def make_bundle(caps=None, base_text=BASE_TEXT, head_text=HEAD_TEXT, snapshot=None):
    snapshot = snapshot or local_snapshot()
    decision = build(caps or HERO_CAPS, base_text=base_text, head_text=head_text,
                     snapshot=snapshot)
    owner, repo = snapshot.repository.split("/")
    case_id = build_case_id(owner, repo, snapshot.pr_number)
    analysis_id = build_analysis_id(
        case_id=case_id, base_sha=snapshot.base_sha, head_sha=snapshot.head_sha,
        corpus_digest=snapshot.corpus_digest, policy_version=snapshot.policy_version,
        parser_version=snapshot.parser_version, semantic_model="anthropic/claude-sonnet-4.5",
        semantic_prompt_version="retention-extract-1.0.0",
        decision_policy_version="scoped-retention-decision-1.0.0",
    )
    return AnalysisBundle(snapshot, analysis_from(caps or HERO_CAPS), decision,
                          case_id, analysis_id, {})


#: sentinel so a test can ask for "no writer at all" rather than "use a fake"
DEFAULT = object()


def make_workflow(bundle, status_writer=DEFAULT, slack_writer=DEFAULT,
                  github_reader=None, drive=None):
    return Workflow(
        settings=None,
        registry=load_registry(),
        github_reader=github_reader or FakeGitHubReader(),
        drive=drive or FakeDriveReader(),
        text_provider=local_text_provider(),
        status_writer=FakeStatusWriter() if status_writer is DEFAULT else status_writer,
        slack_writer=FakeSlackWriter() if slack_writer is DEFAULT else slack_writer,
    )


def run_execute(caps=None, **kwargs):
    bundle = make_bundle(caps)
    workflow = make_workflow(bundle, **kwargs)
    plan = workflow.build_plan(bundle)
    return workflow.execute(bundle, plan), workflow, plan, bundle


@pytest.fixture()
def hero_execution():
    return run_execute()


# ------------------------------------------------------------- E01 and E02

def test_e01_default_cli_mode_performs_no_writes():
    """The product command must not write unless asked."""
    source = (ROOT / "clauseci" / "run.py").read_text()
    assert '"--execute", action="store_true"' in source
    assert "if not args.execute:" in source
    assert "DRY RUN" in source
    # the writers start as None and are only replaced under --execute
    assert source.index("status_writer = None") < source.index("if args.execute:")
    assert source.index("slack_writer = None") < source.index("if args.execute:")


def test_e02_writers_are_only_constructed_under_execute():
    source = (ROOT / "clauseci" / "run.py").read_text()
    execute_block = source.split("if args.execute:")[1].split("workflow = Workflow")[0]
    assert "GitHubStatusWriter(settings)" in execute_block
    assert "SlackCaseWriter(" in execute_block
    # and nowhere else
    assert source.count("GitHubStatusWriter(") == 1
    assert source.count("SlackCaseWriter(") == 1


def test_e02_a_workflow_without_writers_cannot_mutate():
    bundle = make_bundle()
    workflow = make_workflow(bundle, status_writer=None, slack_writer=None)
    receipt = workflow.execute(bundle)
    assert receipt.execution_state is ExecutionState.FAILED
    assert "no status writer" in receipt.github_effect.error


# ------------------------------------------------------------- E03 and E04

def test_e03_status_context_is_exactly_the_required_string(hero_execution):
    _, workflow, plan, _ = hero_execution
    assert plan.github_effect.context == "ClauseCI / retention-compliance"
    assert plan.github_effect.context == RETENTION_STATUS_CONTEXT
    assert plan.github_effect.context != SMOKE_TEST_STATUS_CONTEXT
    assert workflow.status_writer.writes[0]["context"] == RETENTION_STATUS_CONTEXT


def test_e03_no_alternate_product_context_exists_in_the_runtime():
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        if "ClauseCI /" in body and path.name != "versions.py":
            assert "RETENTION_STATUS_CONTEXT" in body or "smoke" in body.lower(), (
                f"{path} spells a status context literally instead of importing it"
            )


def test_e04_status_targets_the_analyzed_head_sha(hero_execution):
    receipt, workflow, plan, bundle = hero_execution
    assert plan.github_effect.target_sha == bundle.snapshot.head_sha
    assert workflow.status_writer.writes[0]["sha"] == bundle.snapshot.head_sha
    assert receipt.github_effect.normalized_observed_payload["sha"] == bundle.snapshot.head_sha


def test_e04_a_partial_sha_is_refused():
    writer = FakeStatusWriter()
    with pytest.raises(RuntimeError):
        writer.set_commit_status("Pranavsingh431", "clauseci-demo-saas", "abc1234",
                                 state="failure", context="x", description="y")


# --------------------------------------------------------------- E05 to E08

@pytest.mark.parametrize(
    "caps,expected_decision,expected_state,expects_slack",
    [
        (None, DecisionState.CONFLICT, GithubStatusState.FAILURE, True),
        ({**HERO_CAPS, "acme-corp": {"application_logs": 90, "diagnostic_logs": 90,
                                     "audit_logs": 30}},
         DecisionState.PASS_SCOPED, GithubStatusState.SUCCESS, False),
        ({"globex": HERO_CAPS["globex"], "acme-labs": HERO_CAPS["acme-labs"]},
         DecisionState.REVIEW_REQUIRED, GithubStatusState.FAILURE, True),
    ],
)
def test_e05_to_e07_decision_maps_to_documented_effects(
    caps, expected_decision, expected_state, expects_slack
):
    receipt, workflow, plan, _ = run_execute(caps)
    assert plan.actual_decision is expected_decision
    assert plan.github_effect.state is expected_state
    assert plan.requires_slack is expects_slack
    assert (workflow.slack_writer.posts > 0) is expects_slack


def test_e08_no_supported_change_maps_to_success_and_no_case():
    from clauseci.domain.snapshot import build_analysis_snapshot
    github = FakeGitHubReader(changed_files=[
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
    ])
    snapshot = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    bundle = make_bundle(head_text=BASE_TEXT, snapshot=snapshot)
    workflow = make_workflow(bundle)
    plan = workflow.build_plan(bundle)
    receipt = workflow.execute(bundle, plan)
    assert plan.actual_decision is DecisionState.NO_SUPPORTED_CHANGE
    assert plan.github_effect.state is GithubStatusState.SUCCESS
    assert plan.slack_effect.action is SlackAction.NONE
    assert workflow.slack_writer.posts == 0
    assert receipt.execution_state is ExecutionState.VERIFIED


# ------------------------------------------------------------- E09 and E10

def test_e09_conflict_requires_a_slack_case(hero_execution):
    receipt, workflow, plan, _ = hero_execution
    assert plan.requires_slack is True
    assert workflow.slack_writer.posts == 1
    assert receipt.slack_effect is not None
    assert receipt.slack_effect.matched


def test_e10_a_clean_pass_creates_no_slack_case():
    safe = {**HERO_CAPS, "acme-corp": {"application_logs": 90, "diagnostic_logs": 90,
                                       "audit_logs": 30}}
    receipt, workflow, plan, _ = run_execute(safe)
    assert plan.actual_decision is DecisionState.PASS_SCOPED
    assert workflow.slack_writer.posts == 0
    assert receipt.slack_effect is None
    assert receipt.execution_state is ExecutionState.VERIFIED
    assert any("no Slack case" in note for note in receipt.notes)


# --------------------------------------------------------------- E11 to E15

def test_e11_case_id_does_not_contain_the_head_sha():
    snapshot = local_snapshot()
    case_id = build_case_id("Pranavsingh431", "clauseci-demo-saas", 1)
    assert snapshot.head_sha not in case_id
    assert snapshot.head_sha[:8] not in case_id
    assert case_id.startswith("cci-")


def test_e12_a_different_head_keeps_the_same_case_id():
    first = build_case_id("Pranavsingh431", "clauseci-demo-saas", 1)
    second = build_case_id("Pranavsingh431", "clauseci-demo-saas", 1)
    assert first == second
    # but the analysis identity does move with the head
    base = dict(case_id=first, base_sha="a" * 40, corpus_digest="c" * 64,
                policy_version="p", parser_version="q", semantic_model="m",
                semantic_prompt_version="v", decision_policy_version="d")
    assert build_analysis_id(head_sha="b" * 40, **base) != \
           build_analysis_id(head_sha="e" * 40, **base)


def test_e12_a_different_pull_request_gets_a_different_case():
    assert build_case_id("o", "r", 1) != build_case_id("o", "r", 2)


def test_e13_the_slack_case_carries_the_stable_marker(hero_execution):
    _, workflow, plan, _ = hero_execution
    text = list(workflow.slack_writer.messages.values())[0]
    assert case_marker(plan.case_id) in text
    assert plan.case_id in text


def test_e14_an_existing_case_is_updated_not_duplicated():
    bundle = make_bundle()
    slack = FakeSlackWriter()
    workflow = make_workflow(bundle, slack_writer=slack)
    plan = workflow.build_plan(bundle)

    workflow.execute(bundle, plan)
    workflow.execute(bundle, plan)

    assert slack.posts == 1, "a second root case must not be created"
    assert slack.updates == 1
    assert len(slack.find_case_by_marker(plan.slack_effect.marker)) == 1


def test_e15_multiple_matching_cases_produce_an_explicit_error():
    bundle = make_bundle()
    slack = FakeSlackWriter()
    workflow = make_workflow(bundle, slack_writer=slack)
    plan = workflow.build_plan(bundle)
    marker = plan.slack_effect.marker
    slack.seed_case(f"an old case {marker}")
    slack.seed_case(f"a duplicate case {marker}")

    receipt = workflow.execute(bundle, plan)
    assert slack.posts == 0, "it must not add a third"
    assert receipt.execution_state is ExecutionState.FAILED
    assert "2 root cases" in receipt.slack_effect.error
    assert "a human must resolve" in receipt.slack_effect.error


# ------------------------------------------------------------- E16 and E17

def test_e16_a_changed_head_prevents_stale_effects():
    bundle = make_bundle()
    moved = FakeGitHubReader()
    moved.get_pull_request = lambda owner, repo, number: {
        "number": number, "title": "t", "html_url": "u",
        "base": {"sha": "a" * 40, "ref": "main"},
        "head": {"sha": "f" * 40, "ref": "feature"},
    }
    status, slack = FakeStatusWriter(), FakeSlackWriter()
    workflow = make_workflow(bundle, status_writer=status, slack_writer=slack,
                             github_reader=moved)
    receipt = workflow.execute(bundle)

    assert receipt.freshness is FreshnessState.SUPERSEDED_BEFORE_EXECUTION
    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert status.writes == [], "no status may be written for a stale head"
    assert slack.posts == 0, "no case may be opened for a stale head"


def test_e17_a_changed_corpus_prevents_stale_effects():
    bundle = make_bundle()
    drive = FakeDriveReader()
    original = drive.list_files

    def revised():
        entries = original()
        entries[0] = {**entries[0], "version": "99"}
        return entries

    drive.list_files = revised
    status, slack = FakeStatusWriter(), FakeSlackWriter()
    workflow = make_workflow(bundle, status_writer=status, slack_writer=slack, drive=drive)
    receipt = workflow.execute(bundle)

    assert receipt.freshness is FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION
    assert receipt.execution_state is ExecutionState.SUPERSEDED
    assert status.writes == []
    assert slack.posts == 0


def test_a_removed_source_also_blocks_execution():
    bundle = make_bundle()
    drive = FakeDriveReader()
    drive.list_files = lambda: FakeDriveReader().list_files()[1:]
    workflow = make_workflow(bundle, drive=drive)
    receipt = workflow.execute(bundle)
    assert receipt.freshness is FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION


# --------------------------------------------------------------- E18 to E23

def test_e18_read_back_on_the_wrong_sha_fails_verification():
    bundle = make_bundle()
    status = FakeStatusWriter()
    status.tamper = lambda record: {**record, "sha": "9" * 40}
    status.latest_status_for_context = lambda o, r, sha, ctx: None
    workflow = make_workflow(bundle, status_writer=status)
    receipt = workflow.execute(bundle)
    assert receipt.github_effect.verification_state is VerificationState.NOT_FOUND
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_e19_read_back_with_the_wrong_context_fails_verification():
    bundle = make_bundle()
    status = FakeStatusWriter()
    status.tamper = lambda record: {**record, "context": "ClauseCI / smoke-test"}
    workflow = make_workflow(bundle, status_writer=status)
    receipt = workflow.execute(bundle)
    assert receipt.github_effect.verification_state is VerificationState.NOT_FOUND
    assert receipt.execution_state is not ExecutionState.VERIFIED


def test_e20_read_back_with_the_wrong_state_fails_verification():
    bundle = make_bundle()
    status = FakeStatusWriter()
    status.tamper = lambda record: {**record, "state": "success"}
    workflow = make_workflow(bundle, status_writer=status)
    receipt = workflow.execute(bundle)
    assert receipt.github_effect.verification_state is VerificationState.MISMATCHED
    assert any("expected 'failure'" in m for m in receipt.github_effect.mismatches)
    assert receipt.execution_state is ExecutionState.PARTIAL


def test_e21_a_case_without_the_marker_fails_verification():
    bundle = make_bundle()
    slack = FakeSlackWriter()
    slack.tamper = lambda text: text.replace("clauseci-case:", "unrelated-marker:")
    workflow = make_workflow(bundle, slack_writer=slack)
    receipt = workflow.execute(bundle)
    assert receipt.slack_effect.verification_state is VerificationState.MISMATCHED
    assert any("case_marker" in m for m in receipt.slack_effect.mismatches)
    assert receipt.execution_state is ExecutionState.PARTIAL


def test_e22_a_case_naming_the_wrong_customer_fails_verification():
    bundle = make_bundle()
    slack = FakeSlackWriter()
    slack.tamper = lambda text: text.replace("acme-corp", "some-other-tenant")
    workflow = make_workflow(bundle, slack_writer=slack)
    receipt = workflow.execute(bundle)
    assert receipt.slack_effect.verification_state is VerificationState.MISMATCHED
    assert any("customer" in m for m in receipt.slack_effect.mismatches)


def test_e23_a_case_stating_the_wrong_decision_fails_verification():
    bundle = make_bundle()
    slack = FakeSlackWriter()
    slack.tamper = lambda text: text.replace("CONFLICT", "PASS_SCOPED")
    workflow = make_workflow(bundle, slack_writer=slack)
    receipt = workflow.execute(bundle)
    assert receipt.slack_effect.verification_state is VerificationState.MISMATCHED
    assert any("decision" in m for m in receipt.slack_effect.mismatches)


def test_an_unrelated_clauseci_message_is_not_accepted():
    """Verification checks meaningful fields, not that some message exists."""
    bundle = make_bundle()
    slack = FakeSlackWriter()
    plan_marker = case_marker(bundle.case_id)
    slack.tamper = lambda text: f"ClauseCI ran. {plan_marker}"
    workflow = make_workflow(bundle, slack_writer=slack)
    receipt = workflow.execute(bundle)
    assert receipt.slack_effect.verification_state is VerificationState.MISMATCHED
    assert len(receipt.slack_effect.mismatches) >= 5


# --------------------------------------------------------------------- E24

def test_e24_github_success_with_slack_failure_cannot_be_verified():
    """A conflict workflow is not complete just because the status was written."""
    bundle = make_bundle()
    status, slack = FakeStatusWriter(), FakeSlackWriter()
    slack.tamper = lambda text: "the case body was lost"
    workflow = make_workflow(bundle, status_writer=status, slack_writer=slack)
    receipt = workflow.execute(bundle)

    assert receipt.github_effect.matched is True
    assert receipt.slack_effect.matched is False
    assert receipt.execution_state is not ExecutionState.VERIFIED
    assert receipt.execution_state is ExecutionState.PARTIAL


def test_e24_a_slack_error_makes_the_workflow_failed():
    bundle = make_bundle()
    slack = FakeSlackWriter()

    def refuse(text):
        raise RuntimeError("channel_not_found")

    slack.post_case = refuse
    workflow = make_workflow(bundle, slack_writer=slack)
    receipt = workflow.execute(bundle)
    assert receipt.execution_state is ExecutionState.FAILED
    assert receipt.github_effect.matched is True


# --------------------------------------------------------------- E25 to E27

def test_e25_receipt_binds_the_exact_head_sha(hero_execution):
    receipt, _, _, bundle = hero_execution
    assert receipt.head_sha == bundle.snapshot.head_sha
    assert len(receipt.head_sha) == 40
    assert receipt.binds(bundle.snapshot.head_sha, bundle.snapshot.corpus_digest)


def test_e25_a_receipt_is_not_reusable_for_another_head(hero_execution):
    receipt, _, _, bundle = hero_execution
    assert not receipt.binds("9" * 40, bundle.snapshot.corpus_digest)


def test_e26_receipt_binds_the_corpus_digest(hero_execution):
    receipt, _, _, bundle = hero_execution
    assert receipt.corpus_digest == bundle.snapshot.corpus_digest
    assert not receipt.binds(bundle.snapshot.head_sha, "0" * 64)


def test_e27_receipt_records_semantic_and_policy_versions(hero_execution):
    receipt, _, _, _ = hero_execution
    versions = receipt.policy_versions
    for key in ("policy_version", "parser_version", "decision_policy_version",
                "execution_policy_version", "semantic_model",
                "semantic_prompt_version", "semantic_schema_version"):
        assert versions.get(key), f"{key} is missing from the receipt"
    assert versions["semantic_model"] == "anthropic/claude-sonnet-4.5"


def test_receipt_round_trips_through_json(hero_execution):
    from clauseci.domain.execution import ExecutionReceipt
    receipt, _, _, _ = hero_execution
    restored = ExecutionReceipt.model_validate_json(receipt.to_json())
    assert restored.execution_state is receipt.execution_state
    assert restored.head_sha == receipt.head_sha


# --------------------------------------------------------------- E28 to E30

def test_e28_the_github_write_adapter_exposes_only_status_operations():
    from clauseci.adapters.github_write import GitHubStatusWriter
    public = {n for n, _ in inspect.getmembers(GitHubStatusWriter, inspect.isfunction)
              if not n.startswith("_")}
    assert public == {"set_commit_status", "read_commit_statuses", "latest_status_for_context"}
    body = (ROOT / "clauseci" / "adapters" / "github_write.py").read_text()
    for banned in ("/merge", "def merge", "def delete", "git push", "/git/refs",
                   "workflow_dispatch", "def close", "def edit"):
        assert banned not in body


def test_e28_the_slack_adapter_exposes_only_case_operations():
    from clauseci.adapters.slack_write import SlackCaseWriter
    public = {n for n, _ in inspect.getmembers(SlackCaseWriter, inspect.isfunction)
              if not n.startswith("_")}
    assert public == {"find_case_by_marker", "post_case", "update_case", "read_case"}
    body = (ROOT / "clauseci" / "adapters" / "slack_write.py").read_text()
    for banned in ("chat_delete", "conversations_create", "admin_", "files_upload",
                   "users_", "chat_postEphemeral"):
        assert banned not in body


def test_e29_no_gmail_operation_exists_in_the_execution_path():
    """
    Bans Gmail operations, not the word. run.py says in its docstring that Gmail
    is not used, which is documentation and must stay.
    """
    for name in ("run.py", "workflow.py", "domain/execution.py", "domain/case_message.py",
                 "adapters/github_write.py", "adapters/slack_write.py"):
        body = (ROOT / "clauseci" / name).read_text()
        for banned in ('build("gmail"', "users().drafts", "drafts().create",
                       "drafts.create", "EmailMessage", "gmail.compose"):
            assert banned not in body, f"{name} contains a Gmail operation: {banned}"


def test_e29_the_execution_path_imports_no_gmail_client():
    for name in ("run.py", "workflow.py", "adapters/github_write.py",
                 "adapters/slack_write.py"):
        body = (ROOT / "clauseci" / name).read_text()
        for line in body.splitlines():
            if line.startswith(("import ", "from ")):
                assert "gmail" not in line.lower()


def test_e30_no_model_object_ever_receives_a_write_adapter():
    """The semantic client is constructed with an API key and nothing else."""
    from clauseci.adapters.openrouter import SemanticClient
    signature = inspect.signature(SemanticClient.__init__)
    assert set(signature.parameters) == {"self", "api_key", "model"}

    structured = inspect.signature(SemanticClient.structured)
    assert set(structured.parameters) == {
        "self", "system", "user", "schema", "schema_name", "usage", "max_tokens"
    }

    body = (ROOT / "clauseci" / "analyzer.py").read_text()
    for banned in ("status_writer", "slack_writer", "GitHubStatusWriter", "SlackCaseWriter"):
        assert banned not in body, f"the analyzer references {banned}"


def test_e30_drive_stays_read_only_in_the_execution_path():
    body = (ROOT / "clauseci" / "adapters" / "drive.py").read_text()
    for banned in ("files().create", "files().update", "files().delete", "MediaFileUpload"):
        assert banned not in body


# --------------------------------------------------- case content quality

def test_the_case_contains_every_field_verification_will_look_for():
    bundle = make_bundle()
    plan = build_execution_plan(decision=bundle.decision, case_id=bundle.case_id,
                                analysis_id=bundle.analysis_id,
                                slack_channel_id="C-TEST", created_at=NOW)
    names = {c.customer_id: c.legal_entity_name for c in load_registry().customers}
    text = render_case(bundle.decision, plan, legal_names=names)
    for name, value in expected_fields(bundle.decision, plan).items():
        assert value in text, f"{name} is missing from the rendered case"


def test_the_case_does_not_dump_the_whole_corpus_or_model_reasoning():
    bundle = make_bundle()
    plan = build_execution_plan(decision=bundle.decision, case_id=bundle.case_id,
                                analysis_id=bundle.analysis_id,
                                slack_channel_id="C-TEST", created_at=NOW)
    text = render_case(bundle.decision, plan)
    assert len(text) < 2200, "the case should be readable in under a minute"
    assert text.count("“") <= 2, "at most one evidence quote"
    for banned in ("reasoning", "chain of thought", "SYSTEM INSTRUCTION",
                   "01_Acme_MSA", "05_Globex_DPA"):
        assert banned not in text


def test_the_case_states_its_scope_and_that_the_correction_is_not_applied():
    bundle = make_bundle()
    plan = build_execution_plan(decision=bundle.decision, case_id=bundle.case_id,
                                analysis_id=bundle.analysis_id,
                                slack_channel_id="C-TEST", created_at=NOW)
    text = render_case(bundle.decision, plan)
    assert "not a statement of legal compliance" in text
    assert "has not been applied" in text
    assert "of 6 requested category outcomes" in text
    assert "of 3 customers fully preserved" in text


def test_the_plan_is_deterministic_for_one_decision():
    bundle = make_bundle()
    first = build_execution_plan(decision=bundle.decision, case_id=bundle.case_id,
                                 analysis_id=bundle.analysis_id,
                                 slack_channel_id="C-TEST", created_at=NOW)
    second = build_execution_plan(decision=bundle.decision, case_id=bundle.case_id,
                                  analysis_id=bundle.analysis_id,
                                  slack_channel_id="C-TEST", created_at=NOW)
    assert first == second
