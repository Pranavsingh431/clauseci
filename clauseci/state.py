"""
Inspecting and reconciling the durable journal.

    python -m clauseci.state inspect --case <case_id>
    python -m clauseci.state inspect --pr 1
    python -m clauseci.state reconcile --case <case_id>

Read only by default. `reconcile` reads provider state to resolve effects whose
outcome is still open, and adopts what it finds. It never creates a new
resource, so it cannot produce a duplicate.

There is deliberately no generic SQL console here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from clauseci.domain.execution import build_case_id
from clauseci.journal import Journal


def _abbrev(value: str | None, keep: int = 12) -> str:
    if not value:
        return "-"
    return value if len(value) <= keep * 2 else f"{value[:keep]}...{value[-6:]}"


def inspect(journal: Journal, case_id: str) -> int:
    case = journal.get_case(case_id)
    if case is None:
        print(f"no case {case_id} in {journal.path}")
        return 1

    print("=" * 96)
    print(f"  CASE  {case.case_id}")
    print("=" * 96)
    print(f"  repository        {case.repository_full_name} PR #{case.pr_number}")
    print(f"  namespace         {case.namespace}")
    print(f"  state             {case.state}")
    print(f"  slack resource    {case.slack_resource_id or 'not recorded'}")
    print(f"  created / updated {case.created_at} / {case.updated_at}")

    if case.resolved_at:
        print(f"  resolved by       {case.resolved_by_analysis_id} at "
              f"{(case.resolved_head_sha or '')[:12]}")
        print(f"  resolved at       {case.resolved_at}")

    analyses = journal.analyses_for_case(case_id)
    receipts = journal._connection.execute(
        "SELECT analysis_id, execution_state, created_at FROM receipts "
        "WHERE case_id = ? ORDER BY created_at", (case_id,)).fetchall()
    receipt_by_analysis = {r["analysis_id"]: r["execution_state"] for r in receipts}

    print(f"\n  LIFECYCLE  ({len(analyses)} analysis/analyses on this one case)")
    for index, row in enumerate(analyses, start=1):
        effects = [e for e in journal.effects_for_case(case_id)
                   if e.analysis_id == row["analysis_id"]]
        print(f"\n    ANALYSIS {index}  {row['analysis_id']}")
        print(f"      head sha    {row['head_sha']}")
        print(f"      decision    {row['decision']}")
        print(f"      receipt     {receipt_by_analysis.get(row['analysis_id'], 'none')}")
        print(f"      effects     " + (", ".join(
            f"{e.provider}:{e.state.value}" for e in effects) or "none"))
    print("")

    effects = journal.effects_for_case(case_id)
    print(f"\n  EFFECTS  ({len(effects)})")
    for effect in effects:
        print(f"    {effect.provider:<7} {effect.action_type:<24} {effect.state.value:<12} "
              f"attempts={effect.attempt_count}")
        print(f"      key       {effect.effect_key}")
        print(f"      target    {effect.target}")
        print(f"      resource  {effect.provider_resource_id or 'not recorded'}")
        print(f"      payload   {_abbrev(effect.intended_payload_digest)}")
        if effect.reconciliation_state:
            print(f"      reconcile {effect.reconciliation_state}")
        if effect.last_error:
            print(f"      error     {effect.last_error[:110]}")

    open_effects = journal.unfinished_effects(case_id)
    print(f"\n  unfinished effects: {len(open_effects)}")

    latest = journal.latest_receipt(case_id)
    if latest:
        receipt = json.loads(latest["receipt_json"])
        print(f"\n  LATEST RECEIPT")
        print(f"    execution state   {receipt['execution_state']}")
        print(f"    head sha          {receipt['head_sha']}")
        print(f"    corpus digest     {_abbrev(receipt['corpus_digest'])}")
        print(f"    freshness         {receipt['freshness']}")
        print(f"    summary           {receipt['verification_summary']}")
    print("=" * 96)
    return 0


def reconcile(journal: Journal, case_id: str | None) -> int:
    from clauseci.adapters.github_write import GitHubStatusWriter
    from clauseci.adapters.slack_write import SlackCaseWriter
    from clauseci.reconcile import reconcile_unfinished_effects
    from clauseci.settings import load_settings

    settings = load_settings()
    channel = os.environ.get("SLACK_ALERT_CHANNEL_ID", "").strip()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    slack = SlackCaseWriter(token, channel) if token and channel else None

    results = reconcile_unfinished_effects(
        journal, slack_writer=slack, status_writer=GitHubStatusWriter(settings),
        case_id=case_id,
    )
    if not results:
        print("  nothing to reconcile. no effect is in an unfinished state.")
        return 0
    print(f"  reconciled {len(results)} unfinished effect(s)")
    for effect, result in results:
        print(f"    {effect.provider:<7} {effect.effect_key}  {result.outcome.value}")
        print(f"      {result.detail}")
        if result.provider_resource_id:
            print(f"      resource {result.provider_resource_id}")
    return 0


def lifecycle_evidence(journal: Journal, case_id: str) -> dict:
    """
    A sanitized lifecycle summary.

    Provider resource ids and commit shas are included because they are the
    evidence. No token, OAuth data or unrelated account information is.
    """
    case = journal.get_case(case_id)
    if case is None:
        raise ValueError(f"no case {case_id}")

    receipts = journal._connection.execute(
        "SELECT analysis_id, head_sha, corpus_digest, execution_state, receipt_json, "
        "created_at FROM receipts WHERE case_id = ? ORDER BY created_at", (case_id,)
    ).fetchall()
    by_analysis = {}
    for row in receipts:
        by_analysis[row["analysis_id"]] = dict(row)

    analyses = []
    for row in journal.analyses_for_case(case_id):
        receipt = by_analysis.get(row["analysis_id"], {})
        effects = [e for e in journal.effects_for_case(case_id)
                   if e.analysis_id == row["analysis_id"]]
        analyses.append({
            "analysis_id": row["analysis_id"],
            "head_sha": row["head_sha"],
            "base_sha": row["base_sha"],
            "corpus_digest": row["corpus_digest"],
            "decision": row["decision"],
            "semantic_model": row["semantic_model"],
            "semantic_prompt_version": row["semantic_prompt_version"],
            "receipt_state": receipt.get("execution_state"),
            "receipt_at": receipt.get("created_at"),
            "effects": [{
                "provider": e.provider, "action": e.action_type,
                "state": e.state.value, "effect_key": e.effect_key,
                "provider_resource_id": e.provider_resource_id,
            } for e in effects],
        })

    return {
        "case_id": case.case_id,
        "repository": case.repository_full_name,
        "pr_number": case.pr_number,
        "case_state": case.state,
        "slack_resource_id": case.slack_resource_id,
        "resolved_by_analysis_id": case.resolved_by_analysis_id,
        "resolved_head_sha": case.resolved_head_sha,
        "resolved_at": case.resolved_at,
        "analyses": analyses,
        "one_case_many_analyses": len(analyses) > 1,
        "distinct_analysis_ids": len({a["analysis_id"] for a in analyses}) == len(analyses),
        "distinct_head_shas": len({a["head_sha"] for a in analyses}) == len(analyses),
        "single_slack_resource": len({
            e["provider_resource_id"] for a in analyses for e in a["effects"]
            if e["provider"] == "slack" and e["provider_resource_id"]
        }) <= 1,
        "statement": (
            "The correction was applied by a developer commit. ClauseCI proposed "
            "it and verified the result. ClauseCI does not commit, push or merge."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clauseci.state")
    parser.add_argument("command", choices=["inspect", "reconcile", "evidence"])
    parser.add_argument("--case", help="case id")
    parser.add_argument("--pr", type=int, help="pull request number, to derive the case id")
    parser.add_argument("--owner", default=os.environ.get("GITHUB_OWNER", ""))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""))
    parser.add_argument("--db", help="journal path")
    args = parser.parse_args(argv)

    from clauseci.settings import load_settings
    load_settings()

    case_id = args.case
    if not case_id and args.pr:
        owner = args.owner or os.environ.get("GITHUB_OWNER", "")
        repo = args.repo or os.environ.get("GITHUB_REPO", "")
        case_id = build_case_id(owner, repo, args.pr)

    journal = Journal(args.db) if args.db else Journal()
    if args.command == "inspect":
        if not case_id:
            print("give --case or --pr")
            return 2
        return inspect(journal, case_id)
    if args.command == "evidence":
        from clauseci.settings import ROOT
        evidence = lifecycle_evidence(journal, case_id)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        out = ROOT / "runs" / "lifecycle"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"lifecycle-{case_id}.json"
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"\n  saved {path.relative_to(ROOT)}")
        return 0
    return reconcile(journal, case_id)


if __name__ == "__main__":
    from clauseci.cli_errors import run_cli

    sys.exit(run_cli(main))
