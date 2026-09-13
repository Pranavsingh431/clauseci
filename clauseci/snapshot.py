"""
Development entry point for building an evidence snapshot.

    python -m clauseci.snapshot --pr https://github.com/owner/repo/pull/1
    python -m clauseci.snapshot --pr 1 --save

Reads GitHub and Google Drive. Writes nothing to either. No model is called.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from clauseci.adapters.drive import DriveReader
from clauseci.adapters.github import GitHubReader
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.snapshot import build_analysis_snapshot
from clauseci.registry import load_registry
from clauseci.settings import ROOT, load_settings

RUNS_DIR = ROOT / "runs"


def abbreviate(identifier: str, keep: int = 8) -> str:
    """Shorten a provider identifier for display. Not a secret, just noisy."""
    if len(identifier) <= keep * 2:
        return identifier
    return f"{identifier[:keep]}...{identifier[-4:]}"


def render(snapshot: AnalysisSnapshot) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add(f"  ClauseCI evidence snapshot   schema {snapshot.snapshot_schema_version}")
    add("=" * 78)
    add("")
    add(f"  repository     {snapshot.repository}  (id {snapshot.repository_id})")
    add(f"  pull request   #{snapshot.pr_number}  {snapshot.pr_title}")
    add(f"  url            {snapshot.pr_url}")
    add(f"  target branch  {snapshot.target_branch}")
    add(f"  base sha       {snapshot.base_sha}")
    add(f"  head sha       {snapshot.head_sha}")
    add(f"  policy         {snapshot.policy_version}   parser {snapshot.parser_version}")
    add("")

    add("  changed files")
    if not snapshot.changed_files:
        add("    (none)")
    for changed in snapshot.changed_files:
        marker = {
            "supported_retention": "SUPPORTED  ",
            "unknown_retention_related": "NEEDS REVIEW",
            "unsupported": "unsupported ",
        }[changed.surface.value]
        add(f"    [{marker}] {changed.path}  ({changed.status}, +{changed.additions}/-{changed.deletions})")
    add("")

    add("  effective retention values")
    add(f"    {'customer':<12} {'category':<18} {'base':>6} {'head':>6}   {'source at head':<28}")
    add(f"    {'-'*12} {'-'*18} {'-'*6} {'-'*6}   {'-'*28}")
    for row in snapshot.summary_rows():
        base = "n/a" if row["base"] is None else f"{row['base']}"
        head = "n/a" if row["head"] is None else f"{row['head']}"
        moved = "  <- changed" if row["base"] != row["head"] else ""
        add(
            f"    {row['customer_id']:<12} {row['category']:<18} {base:>6} {head:>6}   "
            f"{str(row['head_source']):<28}{moved}"
        )
    add("")

    corpus = snapshot.evidence_corpus
    add(f"  evidence corpus   {corpus.source_count} source(s) from folder {abbreviate(corpus.folder_id)}")
    add(f"    {'file id':<16} {'name':<46} {'bytes':>7}  {'content':<10} {'text':<10}")
    add(f"    {'-'*16} {'-'*46} {'-'*7}  {'-'*10} {'-'*10}")
    for source in corpus.sources:
        add(
            f"    {abbreviate(source.file_id, 6):<16} {source.name[:45]:<46} "
            f"{str(source.size_bytes or '?'):>7}  {source.content_sha256[:10]:<10} "
            f"{(source.text_sha256 or 'none')[:10]:<10}"
        )
    add("")
    add(f"  corpus digest  {corpus.corpus_digest}")
    add("")

    if snapshot.collection_warnings:
        add("  warnings")
        for warning in snapshot.collection_warnings:
            add(f"    - {warning}")
        add("")

    add(f"  supported change present: {snapshot.has_supported_change}")
    add("  no GitHub status, Slack message or Gmail action was performed")
    add("=" * 78)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m clauseci.snapshot",
        description="Build a read only evidence snapshot for a pull request.",
    )
    parser.add_argument("--pr", required=True, help="pull request URL, owner/repo#N, or a number")
    parser.add_argument("--save", action="store_true", help="write the snapshot JSON under runs/")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry()
    github = GitHubReader(settings)
    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)

    snapshot = build_analysis_snapshot(args.pr, settings, registry, github, drive)

    if args.json:
        print(snapshot.to_json())
    else:
        print(render(snapshot))

    if args.save:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = RUNS_DIR / f"snapshot-pr{snapshot.pr_number}-{snapshot.head_sha[:8]}-{stamp}.json"
        path.write_text(snapshot.to_json())
        print(f"\nsaved {path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
