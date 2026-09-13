"""
Independent audit of the evaluation metrics.

Recomputes the headline denominators straight from the raw records, without
using evals/metrics.py, and compares. If the two disagree, one of them is wrong
and the audit says so rather than picking a side.

    python -m evals.audit
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW = ROOT / "evals" / "results" / "sanitized-raw.jsonl"
SUMMARY = ROOT / "evals" / "results" / "latest-summary.json"

UNSAFE = {"CONFLICT", "REVIEW_REQUIRED"}
GREEN = {"PASS_SCOPED", "NO_SUPPORTED_CHANGE"}

#: Scenarios that mention a blocking outcome but are deliberately outside the
#: false green denominator, with the reason. Anything not listed here that looks
#: blocking is reported for a human to look at.
DELIBERATE_EXCLUSIONS = {
    "D08": ("candidate A is a hypothetical configuration, not a release "
            "decision. its feasibility being CONFLICT is a correct statement "
            "about a proposal that is never released"),
}


def main() -> int:
    records = [json.loads(line) for line in RAW.read_text().splitlines() if line.strip()]
    summary = json.loads(SUMMARY.read_text())["metrics"]

    with_release = [r for r in records if r.get("expected_release")]
    unsafe = [r for r in with_release if r["expected_release"] in UNSAFE]
    safe = [r for r in with_release if r["expected_release"] == "PASS_SCOPED"]
    false_greens = [r for r in unsafe if r.get("actual_release") in GREEN]

    problems: list[str] = []
    print("=" * 88)
    print("  Independent metric audit, recomputed from raw records")
    print("=" * 88)
    print(f"  raw records                     {len(records)}")
    print(f"  records with a release outcome  {len(with_release)}")
    print()

    print("  FALSE GREEN")
    print(f"    denominator (unsafe or unresolved) {len(unsafe)}")
    print(f"    breakdown                          {dict(Counter(r['expected_release'] for r in unsafe))}")
    print(f"    scenarios                          {sorted(r['scenario_id'] for r in unsafe)}")
    print(f"    false greens                       {len(false_greens)}")
    reported = summary["false_green"]
    if reported["count"] != len(false_greens) or \
            reported["unsafe_or_unresolved_cases"] != len(unsafe):
        problems.append(
            f"false green disagrees: audit {len(false_greens)}/{len(unsafe)}, "
            f"summary {reported['count']}/{reported['unsafe_or_unresolved_cases']}")
    else:
        print(f"    matches the summary                yes")

    # anything that looks blocking but carries no release outcome
    print()
    print("  EXCLUSION CHECK")
    for record in records:
        if record.get("expected_release"):
            continue
        blob = json.dumps(record.get("expected")) + str(record.get("scenario_name"))
        if "CONFLICT" not in blob and "REVIEW_REQUIRED" not in blob:
            continue
        sid = record["scenario_id"]
        if sid in DELIBERATE_EXCLUSIONS:
            print(f"    {sid} excluded on purpose: {DELIBERATE_EXCLUSIONS[sid][:70]}...")
        else:
            problems.append(
                f"{sid} looks blocking but carries no release outcome and is not "
                f"a documented exclusion")

    wrongly_unsafe = [r for r in unsafe if r["expected_release"] == "PASS_SCOPED"]
    if wrongly_unsafe:
        problems.append(f"{len(wrongly_unsafe)} safe scenarios counted as unsafe")

    print()
    print("  SAFE COMPLETION")
    print(f"    denominator (expected PASS_SCOPED) {len(safe)}")
    print(f"    passed                             {sum(1 for r in safe if r['passed'])}")
    print(f"    scenarios                          {sorted(r['scenario_id'] for r in safe)}")
    no_change = [r for r in with_release if r["expected_release"] == "NO_SUPPORTED_CHANGE"]
    print(f"    NO_SUPPORTED_CHANGE excluded       {len(no_change)} record(s)")
    reported = summary["safe_case_completion"]
    if reported["attempted"] != len(safe) or \
            reported["passed"] != sum(1 for r in safe if r["passed"]):
        problems.append("safe completion disagrees with the summary")
    else:
        print(f"    matches the summary                yes")

    # every safe case must actually carry evidence of its outcome
    unevidenced = [r["scenario_id"] for r in safe if not r.get("actual_release")]
    if unevidenced:
        problems.append(f"safe cases with no recorded outcome: {unevidenced}")

    print()
    print("  OTHER METRICS")
    for key, path in (("recovery_success", ("recovery_success",)),
                      ("verified_task_completion", ("verified_task_completion",))):
        block = summary[path[0]]
        print(f"    {key:<26} {block['passed']}/{block['attempted']}")
        if block["attempted"] and block["passed"] > block["attempted"]:
            problems.append(f"{key} numerator exceeds its denominator")

    print()
    print("=" * 88)
    if problems:
        print("  AUDIT FAILED")
        for problem in problems:
            print(f"    - {problem}")
        print("=" * 88)
        return 1
    print("  AUDIT PASSED. recomputed metrics agree with the reported summary.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
