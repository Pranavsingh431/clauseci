"""
The developer correction artifact.

ClauseCI proposes. A developer applies. This module renders the proposal in a
form a person can read and check, and nothing here writes to a repository.

    python -m clauseci.correction --pr 1

The content comes entirely from the Phase 4 preferred candidate. There is no
second correction algorithm and no customer or retention value is written into
this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from clauseci.config_resolution import parse_retention_config
from clauseci.domain.candidates import effective_map
from clauseci.settings import ROOT, SUPPORTED_RETENTION_PATHS

CORRECTIONS_DIR = ROOT / "runs" / "corrections"


class CorrectionError(RuntimeError):
    """The correction could not be produced or did not verify."""


def build_correction(decision, registry, config_path: str = SUPPORTED_RETENTION_PATHS[0]) -> dict:
    """
    Render the preferred candidate as an inspectable correction.

    The proposed document is re-parsed and its effective values recomputed here,
    before any human is asked to apply it. A correction that does not reproduce
    the candidate is refused rather than offered.
    """
    candidate = decision.preferred_candidate
    if candidate is None:
        raise CorrectionError("this decision has no preferred candidate to propose")
    if candidate.proposed_yaml is None:
        raise CorrectionError(f"candidate {candidate.candidate_id} was not rendered")

    cohort = list(decision.coverage.customers_checked)
    keys = {c.customer_id: c.config_key for c in registry.customers}
    recomputed = {
        f"{cid}.{category}": value[0]
        for (cid, category), value in
        effective_map(parse_retention_config(candidate.proposed_yaml), cohort, keys).items()
    }
    expected = {f"{v.customer_id}.{v.category}": v.value for v in candidate.effective_values}
    if recomputed != expected:
        raise CorrectionError(
            "the rendered correction does not recompute to the candidate: "
            f"{ {k: (recomputed.get(k), expected.get(k)) for k in expected if recomputed.get(k) != expected.get(k)} }"
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate.candidate_id,
        "candidate_type": candidate.candidate_type.value,
        "why_preferred": decision.ranking_explanation,
        "source_unsafe_sha": decision.binding.head_sha,
        "repository": decision.binding.repository,
        "pr_number": decision.binding.pr_number,
        "config_path": config_path,
        "proposed_yaml": candidate.proposed_yaml,
        "patch": candidate.patch,
        "effective_values_after": recomputed,
        "requested_outcomes_preserved": candidate.requested_outcomes_preserved,
        "requested_outcomes_total": candidate.requested_outcomes_total,
        "customers_fully_preserved": candidate.customers_fully_preserved,
        "customers_total": candidate.customers_total,
        "applied": False,
        "statement": (
            "This correction has not been applied. ClauseCI does not commit, push "
            "or merge code. A developer applies it as a separate action."
        ),
    }


def render(correction: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 96)
    add(f"  DEVELOPER CORRECTION   candidate {correction['candidate_id']} "
        f"({correction['candidate_type']})")
    add("=" * 96)
    add(f"  repository      {correction['repository']} PR #{correction['pr_number']}")
    add(f"  unsafe commit   {correction['source_unsafe_sha']}")
    add(f"  file            {correction['config_path']}")
    add(f"  why preferred   {correction['why_preferred']}")
    add(f"  preserves       {correction['requested_outcomes_preserved']} of "
        f"{correction['requested_outcomes_total']} requested category outcomes, "
        f"{correction['customers_fully_preserved']} of "
        f"{correction['customers_total']} customers fully")
    add("")
    add("  EFFECTIVE VALUES AFTER THE CORRECTION  (recomputed from the proposed file)")
    for key in sorted(correction["effective_values_after"]):
        add(f"    {key:<34} {correction['effective_values_after'][key]}")
    add("")
    add("  PATCH")
    for line in (correction["patch"] or "").splitlines():
        add(f"    {line}")
    add("")
    add(f"  {correction['statement']}")
    add("=" * 96)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from clauseci.adapters.drive import DriveReader
    from clauseci.adapters.github import GitHubReader
    from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
    from clauseci.domain.semantic_cache import SemanticCache
    from clauseci.registry import load_registry
    from clauseci.settings import load_settings
    from clauseci.workflow import Workflow

    parser = argparse.ArgumentParser(prog="python -m clauseci.correction")
    parser.add_argument("--pr", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    settings, registry = load_settings(), load_registry()
    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)
    workflow = Workflow(
        settings=settings, registry=registry, github_reader=GitHubReader(settings),
        drive=drive, text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
        cache=SemanticCache(enabled=True),
    )
    bundle = workflow.run_analysis(args.pr)
    correction = build_correction(bundle.decision, registry)
    print(render(correction))

    if args.save:
        CORRECTIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = CORRECTIONS_DIR / (
            f"correction-pr{bundle.snapshot.pr_number}-"
            f"{bundle.snapshot.head_sha[:8]}.json")
        path.write_text(json.dumps(correction, indent=2, sort_keys=True))
        yaml_path = path.with_suffix(".yaml")
        yaml_path.write_text(correction["proposed_yaml"])
        print(f"\n  saved {path.relative_to(ROOT)}")
        print(f"  saved {yaml_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
