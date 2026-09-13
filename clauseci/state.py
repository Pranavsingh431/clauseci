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

    analyses = journal.analyses_for_case(case_id)
    print(f"\n  ANALYSES  ({len(analyses)} for this one case)")
    print(f"    {'analysis':<20} {'head sha':<14} {'decision':<20} {'model':<28}")
    print(f"    {'-'*20} {'-'*14} {'-'*20} {'-'*28}")
    for row in analyses:
        print(f"    {row['analysis_id']:<20} {row['head_sha'][:12]:<14} "
              f"{row['decision']:<20} {row['semantic_model']:<28}")

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clauseci.state")
    parser.add_argument("command", choices=["inspect", "reconcile"])
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
    return reconcile(journal, case_id)


if __name__ == "__main__":
    sys.exit(main())
