"""
Phase 4 tests: the deterministic release decision and correction engine.

Phase 4 is deterministic given the obligations, so these tests construct
obligations directly instead of calling a model. That keeps them fast, free and
repeatable, and it isolates the decision logic from semantic variation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from clauseci.config_resolution import parse_retention_config
from clauseci.decide import ReleaseDecision, decide
from clauseci.domain.candidates import FeasibilityState, rank_candidates
from clauseci.domain.decision import DecisionState, Disposition
from clauseci.domain.obligations import (
    Category,
    ObligationAnalysis,
    Operator,
    ResolutionStatus,
    ResolvedRetentionObligation,
    SemanticRunMetadata,
    Unit,
)
from clauseci.domain.rendering import RenderError, render_candidate_yaml, verify_rendering
from clauseci.registry import load_registry
from tests.fakes import FakeDriveReader, FakeGitHubReader, local_snapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

BASE_TEXT = (FIXTURES / "retention_baseline.yaml").read_text()
HEAD_TEXT = (FIXTURES / "retention_requested.yaml").read_text()

#: Caps as the analyzer resolves them on the hero corpus. None means the
#: reviewed sources establish no cap.
HERO_CAPS = {
    "acme-corp": {"application_logs": 30, "diagnostic_logs": 30, "audit_logs": 30},
    "globex": {"application_logs": 90, "diagnostic_logs": 90, "audit_logs": 365},
    "acme-labs": {"application_logs": 180, "diagnostic_logs": 180, "audit_logs": None},
}


def obligations_from(caps: dict[str, dict[str, int | None]]):
    records = []
    for customer_id, categories in caps.items():
        for category, limit in categories.items():
            if limit is None:
                records.append(ResolvedRetentionObligation(
                    customer_id=customer_id, category=Category(category),
                    resolution_status=ResolutionStatus.NO_REPRESENTED_OBLIGATION,
                    review_reason="no executed source states a cap for this category",
                ))
            else:
                records.append(ResolvedRetentionObligation(
                    customer_id=customer_id, category=Category(category),
                    operator=Operator.AT_MOST, value=limit, unit=Unit.DAYS,
                    covered_categories=(Category(category),),
                    controlling_source_id=f"SRC-{customer_id}",
                    controlling_source_file_id=f"file-{customer_id}",
                    source_digest="d" * 64,
                    section="2.1", quote=f"retained for no more than {limit} days",
                    resolution_status=ResolutionStatus.RESOLVED,
                ))
    return records


def analysis_from(caps, warnings=()):
    return ObligationAnalysis(
        snapshot_schema_version="1.0.0",
        policy_version="retention-only-1.0.0",
        corpus_digest="c" * 64,
        customer_cohort=("acme-corp", "globex", "acme-labs"),
        candidates=(),
        resolved=tuple(obligations_from(caps)),
        metadata=SemanticRunMetadata(
            model="anthropic/claude-sonnet-4.5",
            prompt_version="retention-extract-1.0.0",
            schema_version="candidate-obligation-1.0.0",
            obligation_family="RETENTION_UPPER_BOUND",
        ),
        warnings=tuple(warnings),
    )


def build(caps=None, base_text=BASE_TEXT, head_text=HEAD_TEXT, snapshot=None) -> ReleaseDecision:
    return decide(
        snapshot or local_snapshot(),
        analysis_from(caps or HERO_CAPS),
        base_config_text=base_text,
        head_config_text=head_text,
        registry=load_registry(),
        created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )


def finding(decision, customer, category):
    return next(f for f in decision.findings
                if f.customer_id == customer and f.category.value == category)


@pytest.fixture(scope="module")
def hero():
    return build()


# ------------------------------------------------------------- D01 to D09

def test_d01_hero_baseline_is_pass_scoped(hero):
    assert hero.baseline_state is DecisionState.PASS_SCOPED
    assert all(f.disposition is not Disposition.VIOLATED for f in hero.baseline_findings)


def test_d02_hero_requested_head_is_conflict(hero):
    assert hero.actual_head_state is DecisionState.CONFLICT


def test_d03_acme_application_conflict_is_detected(hero):
    record = finding(hero, "acme-corp", "application_logs")
    assert record.disposition is Disposition.VIOLATED
    assert (record.actual_value, record.represented_limit) == (90, 30)
    assert record.introduced_by_pr is True


def test_d04_acme_diagnostic_conflict_is_detected(hero):
    record = finding(hero, "acme-corp", "diagnostic_logs")
    assert record.disposition is Disposition.VIOLATED
    assert record.introduced_by_pr is True


def test_d05_acme_audit_at_thirty_is_satisfied(hero):
    record = finding(hero, "acme-corp", "audit_logs")
    assert record.disposition is Disposition.SATISFIED
    assert (record.actual_value, record.represented_limit) == (30, 30)
    assert record.changed_by_pr is False


@pytest.mark.parametrize("category", ["application_logs", "diagnostic_logs"])
def test_d06_globex_is_satisfied_at_exactly_its_cap(hero, category):
    record = finding(hero, "globex", category)
    assert record.disposition is Disposition.SATISFIED
    assert record.actual_value == record.represented_limit == 90


def test_d07_globex_audit_is_satisfied(hero):
    record = finding(hero, "globex", "audit_logs")
    assert record.disposition is Disposition.SATISFIED
    assert (record.actual_value, record.represented_limit) == (365, 365)


@pytest.mark.parametrize("category", ["application_logs", "diagnostic_logs"])
def test_d08_acme_labs_is_satisfied_well_under_its_cap(hero, category):
    record = finding(hero, "acme-labs", category)
    assert record.disposition is Disposition.SATISFIED
    assert (record.actual_value, record.represented_limit) == (90, 180)


def test_d09_acme_labs_audit_is_not_satisfied_it_is_unrepresented(hero):
    record = finding(hero, "acme-labs", "audit_logs")
    assert record.disposition is Disposition.NO_REPRESENTED_OBLIGATION
    assert record.disposition is not Disposition.SATISFIED
    assert record.represented_limit is None
    assert "not permission" in record.reason


def test_only_the_two_expected_violations_exist(hero):
    violated = {(f.customer_id, f.category.value) for f in hero.findings
                if f.disposition is Disposition.VIOLATED}
    assert violated == {("acme-corp", "application_logs"), ("acme-corp", "diagnostic_logs")}


# ------------------------------------------------------------- D10 to D13

def test_d10_candidate_a_preserves_everything_but_conflicts(hero):
    a = hero.candidate("A")
    assert a.requested_outcomes_preserved == a.requested_outcomes_total == 6
    assert a.feasibility_state is FeasibilityState.CONFLICT
    assert len(a.conflicts) == 2


def test_d11_candidate_b_is_feasible_and_preserves_nothing(hero):
    b = hero.candidate("B")
    assert b.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
    assert b.requested_outcomes_preserved == 0
    assert b.customers_fully_preserved == 0


def test_d12_candidate_c_is_feasible_and_preserves_four_of_six(hero):
    c = hero.candidate("C")
    assert c.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
    assert c.requested_outcomes_preserved == 4
    assert c.requested_outcomes_total == 6
    assert c.customers_fully_preserved == 2
    assert c.customers_total == 3


def test_d13_candidate_c_ranks_above_candidate_b(hero):
    assert hero.preferred_candidate_id == "C"
    b, c = hero.candidate("B"), hero.candidate("C")
    assert c.requested_outcomes_preserved > b.requested_outcomes_preserved


def test_candidate_feasibility_is_recomputed_not_assumed(hero):
    """Every candidate carries its own findings from the same evaluator."""
    for candidate in hero.candidates:
        assert len(candidate.findings) == 9
        violated = [f for f in candidate.findings if f.disposition is Disposition.VIOLATED]
        assert bool(violated) == (candidate.feasibility_state is FeasibilityState.CONFLICT)


def test_feasible_candidates_never_use_the_pass_scoped_name(hero):
    """PASS_SCOPED belongs to a configuration that exists, not a proposal."""
    names = {state.value for state in FeasibilityState}
    assert "PASS_SCOPED" not in names


# ------------------------------------------------------------------- D14

def test_d14_a_safe_candidate_does_not_turn_the_actual_head_green(hero):
    assert hero.preferred_candidate.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
    assert hero.actual_head_state is DecisionState.CONFLICT
    assert finding(hero, "acme-corp", "application_logs").disposition is Disposition.VIOLATED


def test_d14_actual_head_state_is_independent_of_candidate_construction():
    """Removing every candidate must not change the head decision."""
    with_candidates = build()
    stripped = with_candidates.model_copy(
        update={"candidates": (), "preferred_candidate_id": None}
    )
    assert stripped.actual_head_state is with_candidates.actual_head_state
    assert stripped.findings == with_candidates.findings


def test_d14_the_decision_object_keeps_the_two_concepts_in_separate_fields(hero):
    assert "actual_head_state" in ReleaseDecision.model_fields
    assert "preferred_candidate_id" in ReleaseDecision.model_fields
    assert hero.actual_head_state.value == "CONFLICT"
    assert hero.preferred_candidate_id == "C"


# ------------------------------------------------------------- D15 and D16

def test_d15_exactly_at_the_cap_passes():
    caps = {**HERO_CAPS, "acme-corp": {"application_logs": 90, "diagnostic_logs": 90,
                                       "audit_logs": 30}}
    decision = build(caps)
    assert finding(decision, "acme-corp", "application_logs").disposition is Disposition.SATISFIED
    assert decision.actual_head_state is DecisionState.PASS_SCOPED


def test_d16_one_day_over_the_cap_conflicts():
    caps = {**HERO_CAPS, "acme-corp": {"application_logs": 89, "diagnostic_logs": 90,
                                       "audit_logs": 30}}
    decision = build(caps)
    assert finding(decision, "acme-corp", "application_logs").disposition is Disposition.VIOLATED
    assert decision.actual_head_state is DecisionState.CONFLICT


# ------------------------------------------------------------- D17 and D18

def test_d17_missing_obligation_evidence_becomes_review_required():
    caps = {"globex": HERO_CAPS["globex"], "acme-labs": HERO_CAPS["acme-labs"]}
    decision = build(caps)
    record = finding(decision, "acme-corp", "application_logs")
    assert record.disposition is Disposition.REVIEW_REQUIRED
    assert decision.actual_head_state is DecisionState.REVIEW_REQUIRED


def test_d17_unresolved_controlling_source_becomes_review_required():
    analysis = analysis_from(HERO_CAPS)
    unresolved = ResolvedRetentionObligation(
        customer_id="acme-corp", category=Category.APPLICATION_LOGS,
        resolution_status=ResolutionStatus.REVIEW_REQUIRED,
        review_reason="two executed clauses conflict and the language does not settle it",
    )
    patched = analysis.model_copy(update={"resolved": tuple(
        [r for r in analysis.resolved
         if not (r.customer_id == "acme-corp" and r.category is Category.APPLICATION_LOGS)]
        + [unresolved]
    )})
    decision = decide(local_snapshot(), patched, base_config_text=BASE_TEXT,
                      head_config_text=HEAD_TEXT, registry=load_registry())
    assert finding(decision, "acme-corp", "application_logs").disposition is Disposition.REVIEW_REQUIRED


def test_d18_unknown_retention_like_change_becomes_review_required():
    from clauseci.domain.snapshot import build_analysis_snapshot
    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "config/retention_v2.yaml", "status": "added", "additions": 5, "deletions": 0},
    ])
    snapshot = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    safe_caps = {"acme-corp": {"application_logs": 900, "diagnostic_logs": 900, "audit_logs": 900},
                 "globex": HERO_CAPS["globex"], "acme-labs": HERO_CAPS["acme-labs"]}
    decision = build(safe_caps, snapshot=snapshot)
    assert decision.actual_head_state is DecisionState.REVIEW_REQUIRED
    assert "config/retention_v2.yaml" in decision.coverage.unknown_retention_related_files


def test_d18_unknown_surface_cannot_be_green_even_with_everything_else_passing():
    from clauseci.domain.snapshot import build_analysis_snapshot
    github = FakeGitHubReader(changed_files=[
        {"filename": "services/ttl.yaml", "status": "added", "additions": 3, "deletions": 0},
    ])
    snapshot = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    safe_caps = {"acme-corp": {"application_logs": 900, "diagnostic_logs": 900, "audit_logs": 900},
                 "globex": HERO_CAPS["globex"], "acme-labs": HERO_CAPS["acme-labs"]}
    decision = build(safe_caps, snapshot=snapshot)
    assert decision.actual_head_state is not DecisionState.PASS_SCOPED
    assert decision.actual_head_state is not DecisionState.NO_SUPPORTED_CHANGE
    assert decision.actual_head_state is DecisionState.REVIEW_REQUIRED


# ------------------------------------------------------------- D19 and D20

def test_d19_unrelated_only_change_is_no_supported_change():
    from clauseci.domain.snapshot import build_analysis_snapshot
    github = FakeGitHubReader(changed_files=[
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
    ])
    snapshot = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    decision = build(snapshot=snapshot, head_text=BASE_TEXT)
    assert decision.actual_head_state is DecisionState.NO_SUPPORTED_CHANGE


def test_d20_unsupported_files_are_still_recorded_alongside_a_supported_change():
    from clauseci.domain.snapshot import build_analysis_snapshot
    github = FakeGitHubReader(changed_files=[
        {"filename": "config/retention.yaml", "status": "modified", "additions": 2, "deletions": 2},
        {"filename": "README.md", "status": "modified", "additions": 1, "deletions": 0},
        {"filename": "api/public_endpoints.yaml", "status": "modified", "additions": 1, "deletions": 1},
    ])
    snapshot = build_analysis_snapshot("1", None, load_registry(), github, FakeDriveReader())
    decision = build(snapshot=snapshot)
    coverage = decision.coverage
    assert coverage.supported_surfaces_checked == ("config/retention.yaml",)
    assert set(coverage.unsupported_changed_files) == {"README.md", "api/public_endpoints.yaml"}
    assert coverage.unknown_retention_related_files == ()


# ------------------------------------------------------------- D21 and D22

def test_d21_a_pre_existing_conflict_is_not_reported_as_introduced():
    """Baseline already at 200 days against a 30 day cap."""
    over_base = BASE_TEXT.replace("application_logs_days: 30", "application_logs_days: 200", 1)
    over_head = HEAD_TEXT.replace("application_logs_days: 90", "application_logs_days: 200", 1)
    decision = build(base_text=over_base, head_text=over_head)
    record = finding(decision, "acme-corp", "application_logs")
    assert record.disposition is Disposition.VIOLATED
    assert record.present_in_base is True
    assert record.introduced_by_pr is False
    assert "already present at the base revision" in decision.actual_head_reason


def test_d22_a_conflict_the_pull_request_resolves_is_distinguishable():
    """Base violates at 200 days, head brings it back to 30."""
    over_base = BASE_TEXT.replace("application_logs_days: 30", "application_logs_days: 200", 1)
    fixed_head = BASE_TEXT
    decision = build(base_text=over_base, head_text=fixed_head)
    record = finding(decision, "acme-corp", "application_logs")
    assert record.disposition is Disposition.SATISFIED
    assert record.present_in_base is True
    assert record.resolved_by_pr is True
    assert record.introduced_by_pr is False


def test_a_pull_request_that_fixes_one_thing_is_not_globally_green():
    """Head fixes application but leaves diagnostic over the cap."""
    over_base = BASE_TEXT.replace("application_logs_days: 30", "application_logs_days: 200", 1)
    head = HEAD_TEXT.replace("application_logs_days: 90", "application_logs_days: 30", 1)
    decision = build(base_text=over_base, head_text=head)
    assert decision.actual_head_state is DecisionState.CONFLICT


# ------------------------------------------------------------- D23 and D24

def test_d23_ranking_is_deterministic_across_repeated_runs():
    results = {build().preferred_candidate_id for _ in range(5)}
    assert results == {"C"}


def test_d23_ranking_does_not_depend_on_candidate_order(hero):
    forward = rank_candidates(list(hero.candidates))
    backward = rank_candidates(list(reversed(list(hero.candidates))))
    assert forward[0] == backward[0] == "C"


def test_d24_tie_break_prefers_fewer_changed_fields(hero):
    """With preservation equal, the candidate touching less wins."""
    b, c = hero.candidate("B"), hero.candidate("C")
    tied = c.model_copy(update={"requested_outcomes_preserved": b.requested_outcomes_preserved})
    fewer = tied.model_copy(update={"changes_vs_head": tied.changes_vs_head[:1],
                                    "candidate_id": "Z"})
    winner, _ = rank_candidates([b, fewer])
    assert winner == "Z"
    assert len(fewer.changes_vs_head) < len(b.changes_vs_head)


def test_conflicting_candidates_are_rejected_outright(hero):
    winner, _ = rank_candidates([hero.candidate("A")])
    assert winner is None


def test_unknown_never_ranks_above_a_feasible_candidate(hero):
    unknown = hero.candidate("B").model_copy(update={
        "feasibility_state": FeasibilityState.UNKNOWN,
        "requested_outcomes_preserved": 6,
        "candidate_id": "U",
    })
    winner, _ = rank_candidates([unknown, hero.candidate("C")])
    assert winner == "C", "an unresolved candidate must not outrank a known feasible one"


# ------------------------------------------------------------- D25 and D26

def test_d25_rendered_patch_reproduces_the_candidate_effective_state(hero):
    registry = load_registry()
    keys = {c.customer_id: c.config_key for c in registry.customers}
    for candidate in hero.candidates:
        proposed = render_candidate_yaml(HEAD_TEXT, candidate)
        verify_rendering(proposed, candidate, list(hero.coverage.customers_checked), keys)


def test_d25_rendering_verification_catches_a_wrong_document(hero):
    registry = load_registry()
    keys = {c.customer_id: c.config_key for c in registry.customers}
    with pytest.raises(RenderError):
        verify_rendering(BASE_TEXT, hero.candidate("C"),
                         list(hero.coverage.customers_checked), keys)


def test_d25_preferred_candidate_patch_is_minimal(hero):
    patch = hero.preferred_candidate.patch
    added = [line for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in patch.splitlines() if line.startswith("-") and not line.startswith("---")]
    assert len(added) == 2 and len(removed) == 0


def test_d26_the_correction_is_never_applied_to_the_repository(hero):
    """The fixture on disk must be untouched after rendering a correction."""
    assert (FIXTURES / "retention_requested.yaml").read_text() == HEAD_TEXT
    assert hero.preferred_candidate.proposed_yaml != HEAD_TEXT
    parsed = parse_retention_config(hero.preferred_candidate.proposed_yaml)
    assert parsed.customers["acme-corp"]["application_logs_days"] == 30


def test_d26_rendering_writes_no_file(tmp_path, hero):
    before = set(ROOT.rglob("config/retention.yaml"))
    render_candidate_yaml(HEAD_TEXT, hero.candidate("C"))
    assert set(ROOT.rglob("config/retention.yaml")) == before


# ------------------------------------------------------------- D27 to D29

def test_d27_decision_binds_the_exact_head_sha(hero):
    snapshot = local_snapshot()
    assert hero.binding.head_sha == snapshot.head_sha
    assert hero.binding.base_sha == snapshot.base_sha
    assert hero.coverage.head_sha == snapshot.head_sha
    assert len(hero.binding.head_sha) == 40


def test_d28_decision_binds_the_exact_corpus_digest(hero):
    assert hero.binding.corpus_digest == local_snapshot().corpus_digest
    assert hero.coverage.source_snapshot_digest == hero.binding.corpus_digest
    assert len(hero.binding.corpus_digest) == 64


def test_d29_decision_records_the_semantic_model_and_prompt_versions(hero):
    assert hero.binding.semantic_model == "anthropic/claude-sonnet-4.5"
    assert hero.binding.semantic_prompt_version == "retention-extract-1.0.0"
    assert hero.binding.semantic_schema_version == "candidate-obligation-1.0.0"
    assert hero.binding.policy_version and hero.binding.decision_policy_version
    assert hero.binding.parser_version


# ------------------------------------------------------------------- D30

def test_d30_no_represented_obligation_is_never_turned_into_a_number(hero):
    record = finding(hero, "acme-labs", "audit_logs")
    assert record.represented_limit is None
    assert record.disposition is Disposition.NO_REPRESENTED_OBLIGATION
    for candidate in hero.candidates:
        match = next(f for f in candidate.findings
                     if f.customer_id == "acme-labs" and f.category.value == "audit_logs")
        assert match.represented_limit is None
        assert match.disposition is Disposition.NO_REPRESENTED_OBLIGATION


def test_d30_an_unrepresented_category_does_not_block_the_rest(hero):
    """Acme Labs audit is unchanged and unrepresented. It must not be a conflict."""
    assert hero.actual_head_state is DecisionState.CONFLICT
    violated = {(f.customer_id, f.category.value) for f in hero.findings
                if f.disposition is Disposition.VIOLATED}
    assert ("acme-labs", "audit_logs") not in violated
    assert "acme-labs.audit_logs" in hero.coverage.unrepresented_categories


def test_d30_an_unrepresented_changed_category_is_not_silently_passed():
    caps = {**HERO_CAPS,
            "acme-corp": {"application_logs": None, "diagnostic_logs": 30, "audit_logs": 30}}
    decision = build(caps)
    record = finding(decision, "acme-corp", "application_logs")
    assert record.disposition is Disposition.NO_REPRESENTED_OBLIGATION
    assert record.changed_by_pr is True
    assert record.represented_limit is None


# ------------------------------------------------------------- D31 and D32

def test_d31_runtime_never_imports_the_evaluation_oracle():
    for path in (ROOT / "clauseci").rglob("*.py"):
        body = path.read_text()
        assert "ground_truth" not in body
        assert "hero_retention" not in body
        assert "evals" not in body


def test_d32_the_decision_engine_makes_no_external_writes():
    banned = ("chat_postMessage", "drafts.create", "/statuses/", "session.post",
              "session.put", "session.patch", "session.delete", "git commit",
              "git push", "subprocess")
    for name in ("decide.py", "domain/decision.py", "domain/candidates.py",
                 "domain/rendering.py"):
        body = (ROOT / "clauseci" / name).read_text()
        for token in banned:
            assert token not in body, f"{name} contains {token}"


def test_d32_decision_output_contains_no_action_instruction(hero):
    body = hero.to_json().lower()
    for banned in ("post_message", "send_email", "set_status", "merge_pull_request"):
        assert banned not in body


def test_decision_serialises_and_round_trips(hero):
    restored = ReleaseDecision.model_validate_json(hero.to_json())
    assert restored.actual_head_state is hero.actual_head_state
    assert restored.preferred_candidate_id == hero.preferred_candidate_id
    assert restored.binding.head_sha == hero.binding.head_sha


# ------------------------------------------------- held out decision, Part O

HELDOUT_CAPS = {
    **HERO_CAPS,
    # the held out amendment caps application at 45 and covers nothing else
    "acme-corp": {"application_logs": 45, "diagnostic_logs": 30, "audit_logs": 30},
}


def test_heldout_threshold_moves_from_thirty_to_forty_five():
    """
    The Phase 3 held out document says 45 days for application logs only.

    The decision engine must evaluate against 45, not against a remembered 30.
    """
    decision = build(HELDOUT_CAPS)
    record = finding(decision, "acme-corp", "application_logs")
    assert record.represented_limit == 45
    assert record.disposition is Disposition.VIOLATED  # 90 still exceeds 45


@pytest.mark.parametrize("value,expected", [(45, Disposition.SATISFIED),
                                            (46, Disposition.VIOLATED)])
def test_heldout_boundary_is_exactly_at_forty_five(value, expected):
    head = HEAD_TEXT.replace("application_logs_days: 90", f"application_logs_days: {value}", 1)
    decision = build(HELDOUT_CAPS, head_text=head)
    assert finding(decision, "acme-corp", "application_logs").disposition is expected


def test_heldout_proves_the_threshold_comes_from_the_source_not_a_constant():
    """45 passes under the held out cap and fails under the hero cap."""
    head = HEAD_TEXT.replace("application_logs_days: 90", "application_logs_days: 45", 1)
    assert finding(build(HELDOUT_CAPS, head_text=head),
                   "acme-corp", "application_logs").disposition is Disposition.SATISFIED
    assert finding(build(HERO_CAPS, head_text=head),
                   "acme-corp", "application_logs").disposition is Disposition.VIOLATED


def test_no_hero_constant_exists_in_the_decision_engine():
    """No customer name and no cap value may be written into the logic."""
    for name in ("decide.py", "domain/decision.py", "domain/candidates.py",
                 "domain/rendering.py"):
        body = (ROOT / "clauseci" / name).read_text()
        for token in ("acme-corp", "acme-labs", "globex", "Acme", "Globex"):
            assert token not in body, f"{name} hardcodes {token}"
        for number in ("30", "45", "90", "180", "365"):
            assert f" {number}\n" not in body and f"= {number}" not in body


def test_heldout_correction_is_derived_not_assumed():
    """
    Candidate C pins a violating field back to baseline, which is 30 here.

    That is the documented constructor behaviour. What changes with the held out
    source is the threshold, not the correction strategy.
    """
    decision = build(HELDOUT_CAPS)
    candidate = decision.candidate("C")
    assert candidate.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE
    acme = next(v for v in candidate.effective_values
                if v.customer_id == "acme-corp" and v.category == "application_logs")
    assert acme.value == 30


# --------------------------------------------------- oracle cross check

def test_hero_decision_matches_the_independent_oracle(hero):
    oracle = yaml.safe_load((ROOT / "evals" / "ground_truth" / "hero_retention.yaml").read_text())
    field = {"application_logs": "application_logs_days",
             "diagnostic_logs": "diagnostic_logs_days",
             "audit_logs": "audit_logs_days"}
    for record in hero.findings:
        expected = oracle["customers"][record.customer_id]["caps"][field[record.category.value]]
        assert record.represented_limit == expected
    # the oracle names fields with the _days suffix, findings use category names
    for customer_id, expected in oracle["expected_verdict"].items():
        breached = {field[f.category.value] for f in hero.findings
                    if f.customer_id == customer_id and f.disposition is Disposition.VIOLATED}
        assert breached == set(expected["breached_fields"])
        assert (not expected["compliant"]) == bool(breached)
