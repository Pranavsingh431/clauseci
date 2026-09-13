"""
Builds the public sanitized hero lifecycle evidence.

The console reads this file, so a judge can see the whole story without any
private runtime artifact. Provider resource identifiers are replaced by a short
stable hash, which keeps cross record identity checkable without publishing a
personal Slack channel id.

Nothing that matters as evidence is removed: commit SHAs, decisions, case
identity, corpus digests, dispositions and verification outcomes all stay.

    python -m evals.build_hero_evidence
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUNS = ROOT / "runs"
OUT = ROOT / "evals" / "results" / "hero-lifecycle.json"


def stable_hash(value: str | None) -> str | None:
    """A short stable stand in for a provider resource id."""
    if not value:
        return None
    return "res-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def summarise(decision: dict) -> dict:
    return {
        "head_sha": decision["binding"]["head_sha"],
        "base_sha": decision["binding"]["base_sha"],
        "decision": decision["actual_head_state"],
        "reason": decision["actual_head_reason"],
        "baseline_state": decision["baseline_state"],
        "corpus_digest": decision["binding"]["corpus_digest"],
        "semantic_model": decision["binding"]["semantic_model"],
        "semantic_prompt_version": decision["binding"]["semantic_prompt_version"],
        "decision_policy_version": decision["binding"]["decision_policy_version"],
        "findings": [
            {
                "customer_id": f["customer_id"],
                "category": f["category"],
                "actual_value": f["actual_value"],
                "represented_limit": f["represented_limit"],
                "disposition": f["disposition"],
                "introduced_by_pr": f["introduced_by_pr"],
                "controlling_source_id": f["controlling_source_id"],
                "section": f["section"],
                "quote": f["quote"],
            }
            for f in decision["findings"]
        ],
        "unrepresented_categories": decision["coverage"]["unrepresented_categories"],
        "candidates": [
            {
                "candidate_id": c["candidate_id"],
                "candidate_type": c["candidate_type"],
                "feasibility_state": c["feasibility_state"],
                "requested_outcomes_preserved": c["requested_outcomes_preserved"],
                "requested_outcomes_total": c["requested_outcomes_total"],
                "customers_fully_preserved": c["customers_fully_preserved"],
                "customers_total": c["customers_total"],
                "effective_values": [
                    {"customer_id": v["customer_id"], "category": v["category"],
                     "value": v["value"]}
                    for v in c["effective_values"]
                ],
                "patch": c["patch"],
            }
            for c in decision["candidates"]
        ],
        "preferred_candidate_id": decision["preferred_candidate_id"],
        "ranking_explanation": decision["ranking_explanation"],
    }


def receipt_summary(path: Path) -> dict:
    r = load(path)
    out = {
        "head_sha": r["head_sha"],
        "decision": r["decision"],
        "execution_state": r["execution_state"],
        "freshness": r["freshness"],
        "verification_summary": r["verification_summary"],
        "policy_versions": r["policy_versions"],
        "effects": [],
    }
    for key in ("github_effect", "slack_effect"):
        effect = r.get(key)
        if not effect:
            continue
        out["effects"].append({
            "provider": effect["provider"],
            "action": effect["action"],
            "verification_state": effect["verification_state"],
            "provider_resource": stable_hash(effect.get("provider_resource_id")),
            "observed_state": effect.get("normalized_observed_payload", {}).get("state"),
            "effect_key": effect.get("effect_key"),
            "journal_state": effect.get("journal_state"),
        })
    return out


def main() -> int:
    lifecycle = load(RUNS / "lifecycle" / "lifecycle-cci-612892eccea4.json")
    unsafe = load(RUNS / "decision-pr1-ed423b0b.json")
    corrected = load(RUNS / "decision-pr1-a47f5657.json")
    correction = load(RUNS / "corrections" / "correction-pr1-ed423b0b.json")

    receipts = sorted((RUNS / "receipts").glob("*.json"))
    unsafe_receipt = next(r for r in receipts if "ed423b0b" in r.name)
    corrected_receipt = next(r for r in receipts if "a47f5657" in r.name)

    evidence = {
        "artifact_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "captured live provider evidence from the real runs",
        "repository": lifecycle["repository"],
        "pr_number": lifecycle["pr_number"],
        "case_id": lifecycle["case_id"],
        "case_state": lifecycle["case_state"],
        "slack_resource": stable_hash(lifecycle["slack_resource_id"]),
        "resolved_by_analysis_id": lifecycle["resolved_by_analysis_id"],
        "resolved_head_sha": lifecycle["resolved_head_sha"],
        "analyses": [
            {k: v for k, v in a.items() if k != "effects"} | {
                "effects": [
                    {**e, "provider_resource_id": stable_hash(e.get("provider_resource_id"))}
                    for e in a.get("effects", [])
                ]
            }
            for a in lifecycle["analyses"]
        ],
        "invariants": {
            "one_case_many_analyses": lifecycle["one_case_many_analyses"],
            "distinct_analysis_ids": lifecycle["distinct_analysis_ids"],
            "distinct_head_shas": lifecycle["distinct_head_shas"],
            "single_slack_resource": lifecycle["single_slack_resource"],
        },
        "unsafe": summarise(unsafe),
        "corrected": summarise(corrected),
        "unsafe_receipt": receipt_summary(unsafe_receipt),
        "corrected_receipt": receipt_summary(corrected_receipt),
        "correction": {
            "candidate_id": correction["candidate_id"],
            "why_preferred": correction["why_preferred"],
            "patch": correction["patch"],
            "proposed_yaml": correction["proposed_yaml"],
            "effective_values_after": correction["effective_values_after"],
            "applied_by": "a developer, in a separate commit",
            "statement": correction["statement"],
        },
        "provider_state_note": (
            "These are captured results from the real runs. The console can also "
            "read current provider state separately, which is labelled as such."
        ),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  unsafe    {evidence['unsafe']['head_sha'][:8]} {evidence['unsafe']['decision']}")
    print(f"  corrected {evidence['corrected']['head_sha'][:8]} {evidence['corrected']['decision']}")
    print(f"  case      {evidence['case_id']} {evidence['case_state']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
