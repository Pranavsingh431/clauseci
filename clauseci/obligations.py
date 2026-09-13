"""
Development entry point for the obligation analyzer.

    python -m clauseci.obligations --pr https://github.com/owner/repo/pull/1
    python -m clauseci.obligations --snapshot runs/snapshot-pr1-....json
    python -m clauseci.obligations --pr 1 --no-cache --save

Reads GitHub and Google Drive, and calls OpenRouter. Writes nothing to GitHub,
Slack, Gmail or Drive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from clauseci.adapters.drive import DriveReader
from clauseci.adapters.github import GitHubReader
from clauseci.analyzer import analyze_retention_obligations
from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.obligations import ObligationAnalysis, ResolutionStatus
from clauseci.domain.semantic_cache import SemanticCache
from clauseci.domain.snapshot import build_analysis_snapshot
from clauseci.registry import load_registry
from clauseci.settings import ROOT, load_settings

RUNS_DIR = ROOT / "runs"
QUOTE_PREVIEW = 92


def render(analysis: ObligationAnalysis) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 96)
    add("  ClauseCI retention obligations")
    add("=" * 96)
    add(f"  model           {analysis.metadata.model}")
    add(f"  prompt version  {analysis.metadata.prompt_version}")
    add(f"  schema version  {analysis.metadata.schema_version}")
    add(f"  corpus digest   {analysis.corpus_digest[:24]}...")
    add("")

    for customer_id in analysis.customer_cohort:
        add(f"  {customer_id}")
        for record in analysis.for_customer(customer_id):
            if record.resolution_status is ResolutionStatus.RESOLVED:
                verdict = f"<= {record.value} {record.unit.value}"
                source = record.controlling_source_id or ""
                where = record.section or ""
            else:
                verdict = record.resolution_status.value
                source = ""
                where = ""
            add(f"    {record.category.value:<18} {verdict:<28} {source:<26} {where}")
            if record.quote:
                quote = " ".join(record.quote.split())
                add(f"      evidence  \"{quote[:QUOTE_PREVIEW]}{'...' if len(quote) > QUOTE_PREVIEW else ''}\"")
            if record.review_reason and record.resolution_status is not ResolutionStatus.RESOLVED:
                add(f"      reason    {record.review_reason}")
            for rejected in record.rejected_sources:
                add(f"      rejected  {rejected.source_id}: {rejected.reason}")
        add("")

    meta = analysis.metadata
    add(f"  requests {meta.requests} (repairs {meta.repairs})   "
        f"cache {meta.cache_hits} hit / {meta.cache_misses} miss")
    add(f"  tokens   {meta.total_tokens}   "
        f"cost {'$%.4f' % meta.cost_usd if meta.cost_usd is not None else 'not reported'}   "
        f"latency total {meta.latency_seconds_total}s, slowest {meta.latency_seconds_max}s")

    if analysis.warnings:
        add("")
        add("  warnings")
        for warning in analysis.warnings:
            add(f"    - {warning}")

    add("")
    add("  no GitHub status, Slack message, Gmail action or Drive write occurred")
    add("=" * 96)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m clauseci.obligations",
        description="Extract validated retention obligations from a snapshot.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pr", help="pull request URL, owner/repo#N, or a number")
    group.add_argument("--snapshot", help="path to a saved snapshot JSON file")
    parser.add_argument("--no-cache", action="store_true", help="bypass the semantic cache")
    parser.add_argument("--save", action="store_true", help="write the analysis JSON under runs/")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry()
    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)

    if args.snapshot:
        snapshot = AnalysisSnapshot.from_json(Path(args.snapshot).read_text())
    else:
        snapshot = build_analysis_snapshot(
            args.pr, settings, registry, GitHubReader(settings), drive
        )

    analysis = analyze_retention_obligations(
        snapshot,
        text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
        registry=registry,
        cache=SemanticCache(enabled=not args.no_cache),
    )

    print(analysis.to_json() if args.json else render(analysis))

    if args.save:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"obligations-pr{snapshot.pr_number}-{snapshot.head_sha[:8]}.json"
        path.write_text(analysis.to_json())
        print(f"\nsaved {path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    from clauseci.cli_errors import run_cli

    sys.exit(run_cli(main))
