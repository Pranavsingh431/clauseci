"""
The ClauseCI product command.

    python -m clauseci.run --pr <PR_URL>              analyse and print, no writes
    python -m clauseci.run --pr <PR_URL> --execute    also publish the decision

Read only by default. External writes happen only when --execute is given, only
after a freshness recheck, and only to GitHub commit statuses and one Slack
engineering case. Google Drive is read only. Gmail is not used.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from clauseci.adapters.drive import DriveReader
from clauseci.adapters.github import GitHubReader
from clauseci.adapters.github_write import GitHubStatusWriter
from clauseci.adapters.slack_write import SlackCaseWriter
from clauseci.decide import render as render_decision
from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
from clauseci.domain.execution import ExecutionState
from clauseci.domain.semantic_cache import SemanticCache
from clauseci.registry import load_registry
from clauseci.settings import ROOT, load_settings
from clauseci.workflow import Workflow, save_receipt


def render_receipt(receipt, plan) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 100)
    add(f"  EXECUTION RECEIPT   {receipt.execution_state.value}")
    add("=" * 100)
    add(f"  case            {receipt.case_id}")
    add(f"  analysis        {receipt.analysis_id}")
    add(f"  repository      {receipt.repository} PR #{receipt.pr_number}")
    add(f"  head sha        {receipt.head_sha}")
    add(f"  corpus digest   {receipt.corpus_digest}")
    add(f"  decision        {receipt.decision.value}")
    add(f"  freshness       {receipt.freshness.value}")
    add("")
    for effect in (receipt.github_effect, receipt.slack_effect):
        if effect is None:
            continue
        add(f"  {effect.provider:<7} {effect.action:<22} {effect.verification_state.value}")
        add(f"          target    {effect.target}")
        if effect.provider_resource_id:
            add(f"          resource  {effect.provider_resource_id}")
        for key, value in sorted(effect.normalized_observed_payload.items()):
            add(f"          observed  {key} = {value}")
        for mismatch in effect.mismatches:
            add(f"          MISMATCH  {mismatch}")
        if effect.error:
            add(f"          ERROR     {effect.error}")
        add("")
    for note in receipt.notes:
        add(f"  note  {note}")
    add(f"  {receipt.verification_summary}")
    add("")
    add("  policy versions")
    for key, value in sorted(receipt.policy_versions.items()):
        add(f"    {key:<26} {value}")
    add("=" * 100)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m clauseci.run",
        description="Analyse a pull request against customer retention promises.",
    )
    parser.add_argument("--pr", required=True, help="pull request URL, owner/repo#N, or a number")
    parser.add_argument(
        "--execute", action="store_true",
        help="publish the decision to GitHub and Slack. Without this nothing is written.",
    )
    parser.add_argument("--no-cache", action="store_true", help="bypass the semantic cache")
    parser.add_argument("--quiet", action="store_true", help="print the receipt only")
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry()
    github_reader = GitHubReader(settings)
    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)

    status_writer = None
    slack_writer = None
    if args.execute:
        status_writer = GitHubStatusWriter(settings)
        channel = os.environ.get("SLACK_ALERT_CHANNEL_ID", "").strip()
        token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not channel or not token:
            print("SLACK_ALERT_CHANNEL_ID and SLACK_BOT_TOKEN must both be set to execute")
            return 2
        slack_writer = SlackCaseWriter(token, channel)

    workflow = Workflow(
        settings=settings,
        registry=registry,
        github_reader=github_reader,
        drive=drive,
        text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
        status_writer=status_writer,
        slack_writer=slack_writer,
        cache=SemanticCache(enabled=not args.no_cache),
    )

    started = time.monotonic()
    bundle = workflow.run_analysis(args.pr)
    if not args.quiet:
        print(render_decision(bundle.decision))

    if not args.execute:
        print("\n  DRY RUN. No GitHub status, Slack message or Drive write was performed.")
        print("  Re-run with --execute to publish this decision.")
        return 0

    plan = workflow.build_plan(bundle)
    print("")
    print("  Executing ClauseCI decision for:")
    print(f"    repository            {plan.repository}")
    print(f"    pull request          #{plan.pr_number}")
    print(f"    head sha              {plan.head_sha}")
    print(f"    decision              {plan.actual_decision.value}")
    print(f"    GitHub status context {plan.github_effect.context}")
    print(f"    GitHub status state   {plan.github_effect.state.value}")
    print(f"    Slack channel         {plan.slack_effect.channel_id}")
    print(f"    Slack action          {plan.slack_effect.action.value}")
    print("")

    execution_started = time.monotonic()
    receipt = workflow.execute(bundle, plan)
    execution_seconds = time.monotonic() - execution_started

    print(render_receipt(receipt, plan))
    path = save_receipt(receipt)
    print(f"\n  receipt saved to {path.relative_to(ROOT)}")

    timings = bundle.timings
    print(f"\n  latency  snapshot {timings['snapshot']:.2f}s   "
          f"semantic {timings['semantic']:.2f}s   "
          f"decision {timings['decision'] * 1000:.0f}ms   "
          f"execution and verification {execution_seconds:.2f}s   "
          f"total {time.monotonic() - started:.2f}s")

    return 0 if receipt.execution_state is ExecutionState.VERIFIED else 1


if __name__ == "__main__":
    from clauseci.cli_errors import run_cli

    sys.exit(run_cli(main))
