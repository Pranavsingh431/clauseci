"""
The scoped release decision.

    python -m clauseci.decide --pr https://github.com/owner/repo/pull/1

Combines the Phase 2 evidence snapshot with the Phase 3 validated obligations
and produces a deterministic decision plus three bounded correction candidates.

Read only. Nothing is written to GitHub, Slack, Gmail or Drive, and no
correction is ever applied.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from clauseci.config_resolution import parse_retention_config
from clauseci.domain.candidates import (
    Candidate,
    FeasibilityState,
    RequestedOutcome,
    build_candidates,
    effective_map,
    rank_candidates,
    requested_outcomes,
)
from clauseci.domain.decision import (
    Coverage,
    DecisionState,
    Disposition,
    EvidenceBinding,
    Finding,
    decide_state,
    evaluate_findings,
)
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.obligations import ObligationAnalysis, ResolutionStatus
from clauseci.registry import CustomerRegistry, load_registry
from clauseci.settings import ROOT, SUPPORTED_RETENTION_PATHS, load_settings
from clauseci.versions import (
    DECISION_POLICY_VERSION,
    DECISION_SCHEMA_VERSION,
)

RUNS_DIR = ROOT / "runs"


class ReleaseDecision(BaseModel):
    """A scoped release decision. Not a statement of legal compliance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_schema_version: str
    created_at: datetime

    #: The decision for the configuration that actually exists at head.
    actual_head_state: DecisionState
    actual_head_reason: str
    #: The same evaluation applied to the base revision, so an introduced
    #: problem can be told apart from one that was already there.
    baseline_state: DecisionState
    baseline_reason: str

    findings: tuple[Finding, ...]
    baseline_findings: tuple[Finding, ...]
    requested_outcomes: tuple[RequestedOutcome, ...]

    candidates: tuple[Candidate, ...]
    #: A candidate is a proposal. It never changes actual_head_state.
    preferred_candidate_id: str | None
    ranking_explanation: str

    coverage: Coverage
    binding: EvidenceBinding
    warnings: tuple[str, ...] = ()

    def candidate(self, candidate_id: str) -> Candidate | None:
        for entry in self.candidates:
            if entry.candidate_id == candidate_id:
                return entry
        return None

    @property
    def preferred_candidate(self) -> Candidate | None:
        return self.candidate(self.preferred_candidate_id) if self.preferred_candidate_id else None

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=True)


def decide(
    snapshot: AnalysisSnapshot,
    analysis: ObligationAnalysis,
    *,
    base_config_text: str,
    head_config_text: str,
    registry: CustomerRegistry | None = None,
    config_path: str = SUPPORTED_RETENTION_PATHS[0],
    created_at: datetime | None = None,
) -> ReleaseDecision:
    """Produce the scoped decision. Deterministic given the same inputs."""
    registry = registry or load_registry()
    cohort = list(snapshot.customer_cohort)
    config_keys = {c.customer_id: c.config_key for c in registry.customers}
    obligations = list(analysis.resolved)
    warnings: list[str] = []

    base_config = parse_retention_config(base_config_text)
    head_config = parse_retention_config(head_config_text)

    base_effective = effective_map(base_config, cohort, config_keys)
    head_effective = effective_map(head_config, cohort, config_keys)
    base_values = {k: v[0] for k, v in base_effective.items()}

    baseline_findings = evaluate_findings(base_effective, obligations)
    head_findings = evaluate_findings(head_effective, obligations, base_effective=base_values)

    extra_review: list[str] = []
    if snapshot.evidence_corpus.missing_documents:
        extra_review.append(
            "expected evidence is missing from the corpus: "
            + ", ".join(snapshot.evidence_corpus.missing_documents)
        )
    for warning in snapshot.collection_warnings:
        warnings.append(f"snapshot: {warning}")
    for warning in analysis.warnings:
        warnings.append(f"analysis: {warning}")

    inventory_known = True
    baseline_state, baseline_reason = decide_state(
        baseline_findings,
        supported_changed=snapshot.supported_changed_surfaces,
        unknown_retention_changed=(),
        inventory_known=inventory_known,
        extra_review_reasons=tuple(extra_review),
    )
    head_state, head_reason = decide_state(
        head_findings,
        supported_changed=snapshot.supported_changed_surfaces,
        unknown_retention_changed=snapshot.unknown_retention_changed_surfaces,
        inventory_known=inventory_known,
        extra_review_reasons=tuple(extra_review),
    )

    candidates = build_candidates(
        base_config=base_config,
        head_config=head_config,
        cohort=cohort,
        config_keys=config_keys,
        obligations=obligations,
        head_findings=head_findings,
    )
    preferred_id, ranking_reason = rank_candidates(candidates)

    from clauseci.domain.rendering import (
        RenderError,
        render_candidate_yaml,
        render_patch,
        verify_rendering,
    )

    rendered: list[Candidate] = []
    for entry in candidates:
        try:
            proposed = render_candidate_yaml(head_config_text, entry)
            verify_rendering(proposed, entry, cohort, config_keys)
            patch = render_patch(head_config_text, proposed, config_path)
        except RenderError as exc:
            warnings.append(f"candidate {entry.candidate_id} could not be rendered: {exc}")
            rendered.append(entry)
        else:
            rendered.append(entry.model_copy(update={"proposed_yaml": proposed, "patch": patch}))

    unrepresented = sorted({
        f"{f.customer_id}.{f.category.value}" for f in head_findings
        if f.disposition is Disposition.NO_REPRESENTED_OBLIGATION
    })

    coverage = Coverage(
        supported_surfaces_checked=snapshot.supported_changed_surfaces,
        customers_checked=tuple(cohort),
        categories_checked=tuple(sorted({f.category.value for f in head_findings})),
        unrepresented_categories=tuple(unrepresented),
        unsupported_changed_files=snapshot.unsupported_changed_surfaces,
        unknown_retention_related_files=snapshot.unknown_retention_changed_surfaces,
        source_snapshot_digest=snapshot.corpus_digest,
        head_sha=snapshot.head_sha,
    )

    binding = EvidenceBinding(
        repository=snapshot.repository,
        pr_number=snapshot.pr_number,
        base_sha=snapshot.base_sha,
        head_sha=snapshot.head_sha,
        corpus_digest=snapshot.corpus_digest,
        snapshot_schema_version=snapshot.snapshot_schema_version,
        parser_version=snapshot.parser_version,
        policy_version=snapshot.policy_version,
        decision_policy_version=DECISION_POLICY_VERSION,
        decision_schema_version=DECISION_SCHEMA_VERSION,
        semantic_model=analysis.metadata.model,
        semantic_prompt_version=analysis.metadata.prompt_version,
        semantic_schema_version=analysis.metadata.schema_version,
    )

    return ReleaseDecision(
        decision_schema_version=DECISION_SCHEMA_VERSION,
        created_at=created_at or datetime.now(timezone.utc),
        actual_head_state=head_state,
        actual_head_reason=head_reason,
        baseline_state=baseline_state,
        baseline_reason=baseline_reason,
        findings=tuple(head_findings),
        baseline_findings=tuple(baseline_findings),
        requested_outcomes=tuple(requested_outcomes(
            base_values, {k: v[0] for k, v in head_effective.items()}
        )),
        candidates=tuple(rendered),
        preferred_candidate_id=preferred_id,
        ranking_explanation=ranking_reason,
        coverage=coverage,
        binding=binding,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------- rendering

def render(decision: ReleaseDecision) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 100)
    add(f"  ClauseCI scoped release decision   {decision.binding.repository} "
        f"PR #{decision.binding.pr_number}")
    add("=" * 100)

    add("")
    add("  ACTUAL HEAD")
    add(f"    {'customer':<12} {'category':<18} {'actual':>7} {'limit':>7}  "
        f"{'source':<26} disposition")
    add(f"    {'-'*12} {'-'*18} {'-'*7} {'-'*7}  {'-'*26} {'-'*26}")
    for finding in decision.findings:
        limit = "none" if finding.represented_limit is None else str(finding.represented_limit)
        marker = ""
        if finding.introduced_by_pr:
            marker = "  <- introduced by this PR"
        elif finding.disposition is Disposition.VIOLATED:
            marker = "  <- already present at base"
        add(f"    {finding.customer_id:<12} {finding.category.value:<18} "
            f"{str(finding.actual_value):>7} {limit:>7}  "
            f"{(finding.controlling_source_id or ''):<26} "
            f"{finding.disposition.value}{marker}")

    add("")
    add(f"  DECISION   {decision.actual_head_state.value}")
    add(f"    {decision.actual_head_reason}")
    add(f"    baseline at {decision.binding.base_sha[:8]} was "
        f"{decision.baseline_state.value}")

    add("")
    add("  CANDIDATES")
    add(f"    {'id':<3} {'type':<30} {'feasibility':<20} {'preserved':<12} changed fields")
    add(f"    {'-'*3} {'-'*30} {'-'*20} {'-'*12} {'-'*14}")
    for candidate in decision.candidates:
        preserved = (f"{candidate.requested_outcomes_preserved}/"
                     f"{candidate.requested_outcomes_total}")
        add(f"    {candidate.candidate_id:<3} {candidate.candidate_type.value:<30} "
            f"{candidate.feasibility_state.value:<20} {preserved:<12} "
            f"{len(candidate.changes_vs_head)}")
        if candidate.conflicts:
            for conflict in candidate.conflicts:
                add(f"        conflict: {conflict}")

    add("")
    add(f"  PREFERRED CANDIDATE   {decision.preferred_candidate_id or 'none'}")
    add(f"    {decision.ranking_explanation}")
    preferred = decision.preferred_candidate
    if preferred:
        add(f"    category outcomes preserved  "
            f"{preferred.requested_outcomes_preserved} of {preferred.requested_outcomes_total}")
        add(f"    customers fully preserved    "
            f"{preferred.customers_fully_preserved} of {preferred.customers_total}")
        add("    this is a proposal. it is not applied, and it does not change the "
            "decision above")
        if preferred.patch:
            add("")
            add("  PROPOSED CORRECTION")
            for line in preferred.patch.splitlines():
                add(f"    {line}")

    add("")
    add("  EVIDENCE BINDING")
    binding = decision.binding
    add(f"    head sha        {binding.head_sha}")
    add(f"    base sha        {binding.base_sha}")
    add(f"    corpus digest   {binding.corpus_digest}")
    add(f"    policy          {binding.policy_version} / {binding.decision_policy_version}")
    add(f"    semantic model  {binding.semantic_model}")
    add(f"    prompt version  {binding.semantic_prompt_version}")

    add("")
    add("  COVERAGE")
    coverage = decision.coverage
    add(f"    supported surfaces checked   {', '.join(coverage.supported_surfaces_checked) or 'none'}")
    add(f"    customers checked            {', '.join(coverage.customers_checked)}")
    add(f"    no represented cap for       {', '.join(coverage.unrepresented_categories) or 'none'}")
    add(f"    unsupported changed files    {', '.join(coverage.unsupported_changed_files) or 'none'}")
    add(f"    unrecognised retention files {', '.join(coverage.unknown_retention_related_files) or 'none'}")

    add("")
    add("  This is a scoped release decision over the supported retention surface.")
    add("  It is not a statement of legal compliance.")
    add("  No GitHub status, Slack message, Gmail action or Drive write occurred.")
    add("=" * 100)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from clauseci.adapters.drive import DriveReader
    from clauseci.adapters.github import GitHubReader
    from clauseci.analyzer import analyze_retention_obligations
    from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
    from clauseci.domain.semantic_cache import SemanticCache
    from clauseci.domain.snapshot import build_analysis_snapshot

    parser = argparse.ArgumentParser(
        prog="python -m clauseci.decide",
        description="Produce a scoped release decision for a pull request.",
    )
    parser.add_argument("--pr", required=True, help="pull request URL, owner/repo#N, or a number")
    parser.add_argument("--no-cache", action="store_true", help="bypass the semantic cache")
    parser.add_argument("--save", action="store_true", help="write the decision JSON under runs/")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    args = parser.parse_args(argv)

    settings = load_settings()
    registry = load_registry()
    github = GitHubReader(settings)
    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)

    snapshot = build_analysis_snapshot(args.pr, settings, registry, github, drive)
    analysis = analyze_retention_obligations(
        snapshot,
        text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
        registry=registry,
        cache=SemanticCache(enabled=not args.no_cache),
    )

    owner, repo = snapshot.repository.split("/")
    path = SUPPORTED_RETENTION_PATHS[0]
    base_text = github.get_file_at_ref(owner, repo, path, snapshot.base_sha)
    head_text = github.get_file_at_ref(owner, repo, path, snapshot.head_sha)
    if base_text is None or head_text is None:
        print(f"{path} is absent at one of the revisions, so no scoped decision is possible")
        return 2

    decision = decide(
        snapshot, analysis,
        base_config_text=base_text.decode("utf-8"),
        head_config_text=head_text.decode("utf-8"),
        registry=registry,
    )

    print(decision.to_json() if args.json else render(decision))

    if args.save:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        out = RUNS_DIR / f"decision-pr{snapshot.pr_number}-{snapshot.head_sha[:8]}.json"
        out.write_text(decision.to_json())
        print(f"\nsaved {out.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    from clauseci.cli_errors import run_cli

    sys.exit(run_cli(main))
