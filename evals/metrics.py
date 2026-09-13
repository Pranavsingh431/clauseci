"""
Metrics, computed from raw evaluation records only.

Nothing in a summary is typed by hand. Every number here has an explicit
denominator, because a numerator on its own can hide the size of the sample.
"""

from __future__ import annotations

from evals.record import Mode

#: A case whose correct answer is "do not release". A false green here is a
#: release critical failure.
#:
#: Only a decision about an ACTUAL head counts. A candidate's feasibility state
#: is also spelled CONFLICT, but a candidate is a hypothetical configuration
#: that is never released, so it does not belong in this denominator. Scenario
#: D08 is the case to remember: candidate A conflicts, and that is a correct
#: result about a proposal, not a release decision. `evals/audit.py` checks that
#: this exclusion is deliberate rather than accidental.
UNSAFE_EXPECTED = {"CONFLICT", "REVIEW_REQUIRED"}
GREEN_RESULTS = {"PASS_SCOPED", "NO_SUPPORTED_CHANGE"}

#: Live scenarios that are whole multi app workflows, as opposed to a replay
#: check, a negative control or a provider hygiene read. Verified task
#: completion is measured over these only, so the numerator and denominator
#: describe the same set.
LIVE_WORKFLOW_IDS = {"L01", "L02"}


def _unique(records, mode):
    return [r for r in records if r["mode"] == mode and r.get("repeat_number", 1) == 1]


def compute(records: list[dict], recoverable_ids: set[str]) -> dict:
    semantic_unique = _unique(records, Mode.SEMANTIC)
    semantic_all = [r for r in records if r["mode"] == Mode.SEMANTIC]
    semantic_repeats = [r for r in semantic_all if r.get("repeat_number", 1) > 1]

    decision = _unique(records, Mode.DECISION)
    kernel = _unique(records, Mode.KERNEL)
    lifecycle = _unique(records, Mode.LIFECYCLE)
    live = [r for r in records if r["mode"] == Mode.LIVE]

    # false greens, across every mode that carries a release expectation
    with_release = [r for r in records if r.get("expected_release")]
    unsafe = [r for r in with_release if r["expected_release"] in UNSAFE_EXPECTED]
    false_greens = [r for r in unsafe if r.get("actual_release") in GREEN_RESULTS]

    safe = [r for r in with_release if r["expected_release"] == "PASS_SCOPED"]
    safe_passed = [r for r in safe if r["passed"]]

    recovery = [r for r in kernel if r["scenario_id"] in recoverable_ids]
    recovery_passed = [r for r in recovery if r["passed"]]

    live_workflows = [r for r in live if r["scenario_id"] in LIVE_WORKFLOW_IDS]
    live_workflows_verified = [r for r in live_workflows if r["passed"]]
    live_verified = [r for r in live if r["passed"]]

    costs = [r["model_cost_usd"] for r in records if r.get("model_cost_usd")]
    tokens = [r["model_tokens"] for r in records if r.get("model_tokens")]
    latencies = [r["latency_ms"] for r in records if r.get("latency_ms")]

    def ratio(num, den):
        return {"passed": len(num), "attempted": len(den),
                "ratio": (len(num) / len(den)) if den else None}

    return {
        "semantic": {
            "unique_scenarios": ratio([r for r in semantic_unique if r["passed"]], semantic_unique),
            "unique_scenario_ids": sorted({r["scenario_id"] for r in semantic_unique}),
            "total_model_dependent_executions": len(semantic_all),
            "repeat_executions": ratio([r for r in semantic_repeats if r["passed"]],
                                       semantic_repeats),
            "failed_scenarios": sorted({r["scenario_id"] for r in semantic_all
                                        if not r["passed"]}),
        },
        "decision": {
            "scenarios": ratio([r for r in decision if r["passed"]], decision),
            "failed_scenarios": sorted({r["scenario_id"] for r in decision if not r["passed"]}),
        },
        "kernel": {
            "scenarios": ratio([r for r in kernel if r["passed"]], kernel),
            "failed_scenarios": sorted({r["scenario_id"] for r in kernel if not r["passed"]}),
        },
        "lifecycle": {
            "scenarios": ratio([r for r in lifecycle if r["passed"]], lifecycle),
            "failed_scenarios": sorted({r["scenario_id"] for r in lifecycle if not r["passed"]}),
        },
        "live": {
            "workflows": ratio(live_verified, live),
            "provider_contacted": sum(1 for r in live if r.get("live_provider_contacted")),
            "failed_scenarios": sorted({r["scenario_id"] for r in live if not r["passed"]}),
        },
        "false_green": {
            "count": len(false_greens),
            "unsafe_or_unresolved_cases": len(unsafe),
            "scenarios": sorted({r["scenario_id"] for r in false_greens}),
        },
        "safe_case_completion": ratio(safe_passed, safe),
        "recovery_success": {
            **ratio(recovery_passed, recovery),
            "recoverable_scenarios": sorted(recoverable_ids),
        },
        "verified_task_completion": {
            **ratio(live_workflows_verified, live_workflows),
            "workflow_scenarios": sorted(LIVE_WORKFLOW_IDS),
            "note": ("measured over whole multi app workflows only, not over "
                     "replay checks, negative controls or hygiene reads"),
        },
        "unintended_effects": {
            "duplicate_slack_root_cases": _count_note(records, "duplicate_slack_root"),
            "wrong_target_writes": _count_note(records, "wrong_target_write"),
            "unexpected_github_contexts": _count_note(records, "unexpected_github_context"),
            "gmail_actions": _count_note(records, "gmail_action"),
            "drive_mutations": _count_note(records, "drive_mutation"),
            "autonomous_merges": _count_note(records, "autonomous_merge"),
            "autonomous_remediation_commits": _count_note(records, "autonomous_remediation"),
            "unknown_outcomes_after_reconciliation": _count_note(records, "unknown_remaining"),
        },
        "cost_and_latency": {
            "model_calls_sampled": len(costs),
            "total_reported_cost_usd": round(sum(costs), 6) if costs else 0.0,
            "total_tokens": sum(t for t in tokens if t),
            "latency_ms_min": min(latencies) if latencies else None,
            "latency_ms_max": max(latencies) if latencies else None,
        },
        "totals": {
            "records": len(records),
            "passed": sum(1 for r in records if r["passed"]),
            "failed": sum(1 for r in records if not r["passed"]),
        },
    }


def _count_note(records, key: str) -> int:
    total = 0
    for record in records:
        observed = record.get("normalized_provider_observations") or {}
        value = observed.get(key)
        if isinstance(value, int):
            total += value
    return total
