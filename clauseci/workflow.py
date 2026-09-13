"""
The thin deterministic workflow layer.

It sequences work that other modules already decided. There is no semantic
reasoning here and no copy of the Phase 4 decision logic. It consumes a
completed decision and carries out its consequences.

Writes happen only when the caller asks for them, only after a freshness
recheck, and only to the two providers the policy names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from clauseci.decide import ReleaseDecision, decide
from clauseci.domain.case_message import expected_fields, render_case
from clauseci.domain.decision import DecisionState
from clauseci.domain.execution import (
    EffectRecord,
    ExecutionPlan,
    ExecutionReceipt,
    ExecutionState,
    FreshnessState,
    GithubStatusState,
    SlackAction,
    VerificationState,
    build_analysis_id,
    build_case_id,
    build_execution_plan,
    payload_digest,
)
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.obligations import ObligationAnalysis
from clauseci.settings import ROOT, SUPPORTED_RETENTION_PATHS
from clauseci.versions import (
    DECISION_POLICY_VERSION,
    EXECUTION_POLICY_VERSION,
    RETENTION_STATUS_CONTEXT,
)

RECEIPT_DIR = ROOT / "runs" / "receipts"


@dataclass
class AnalysisBundle:
    """Everything one read only pass produced."""

    snapshot: AnalysisSnapshot
    analysis: ObligationAnalysis
    decision: ReleaseDecision
    case_id: str
    analysis_id: str
    timings: dict[str, float]


class WorkflowError(RuntimeError):
    """The workflow could not continue."""


class Workflow:
    """Read only by default. Writes only when `execute` is called."""

    def __init__(
        self,
        *,
        settings,
        registry,
        github_reader,
        drive,
        text_provider,
        status_writer=None,
        slack_writer=None,
        semantic_client=None,
        cache=None,
        config_path: str = SUPPORTED_RETENTION_PATHS[0],
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.github_reader = github_reader
        self.drive = drive
        self.text_provider = text_provider
        self.status_writer = status_writer
        self.slack_writer = slack_writer
        self.semantic_client = semantic_client
        self.cache = cache
        self.config_path = config_path

    # ------------------------------------------------------------- analysis

    def run_analysis(self, pr_reference: str) -> AnalysisBundle:
        """Phase 2, then Phase 3, then Phase 4. No external writes."""
        from clauseci.analyzer import analyze_retention_obligations
        from clauseci.domain.snapshot import build_analysis_snapshot

        timings: dict[str, float] = {}

        started = time.monotonic()
        snapshot = build_analysis_snapshot(
            pr_reference, self.settings, self.registry, self.github_reader, self.drive
        )
        timings["snapshot"] = time.monotonic() - started

        started = time.monotonic()
        analysis = analyze_retention_obligations(
            snapshot,
            text_provider=self.text_provider,
            client=self.semantic_client,
            registry=self.registry,
            cache=self.cache,
        )
        timings["semantic"] = time.monotonic() - started

        owner, repo = snapshot.repository.split("/")
        base_raw = self.github_reader.get_file_at_ref(
            owner, repo, self.config_path, snapshot.base_sha
        )
        head_raw = self.github_reader.get_file_at_ref(
            owner, repo, self.config_path, snapshot.head_sha
        )
        if base_raw is None or head_raw is None:
            raise WorkflowError(
                f"{self.config_path} is absent at one revision, so no scoped "
                f"decision is possible"
            )

        started = time.monotonic()
        decision = decide(
            snapshot, analysis,
            base_config_text=base_raw.decode("utf-8"),
            head_config_text=head_raw.decode("utf-8"),
            registry=self.registry,
            config_path=self.config_path,
        )
        timings["decision"] = time.monotonic() - started

        case_id = build_case_id(owner, repo, snapshot.pr_number)
        analysis_id = build_analysis_id(
            case_id=case_id,
            base_sha=snapshot.base_sha,
            head_sha=snapshot.head_sha,
            corpus_digest=snapshot.corpus_digest,
            policy_version=snapshot.policy_version,
            parser_version=snapshot.parser_version,
            semantic_model=analysis.metadata.model,
            semantic_prompt_version=analysis.metadata.prompt_version,
            decision_policy_version=DECISION_POLICY_VERSION,
        )
        return AnalysisBundle(snapshot, analysis, decision, case_id, analysis_id, timings)

    # ------------------------------------------------------------ freshness

    def recheck_freshness(self, bundle: AnalysisBundle) -> tuple[FreshnessState, str]:
        """
        Confirm nothing moved between analysis and execution.

        The pull request head is re-fetched and compared exactly. The Drive
        corpus is checked through provider revision metadata, which changes
        whenever a document is replaced, rather than by downloading every PDF
        again.
        """
        snapshot = bundle.snapshot
        owner, repo = snapshot.repository.split("/")

        pull_request = self.github_reader.get_pull_request(owner, repo, snapshot.pr_number)
        current_head = pull_request["head"]["sha"]
        if current_head != snapshot.head_sha:
            return FreshnessState.SUPERSEDED_BEFORE_EXECUTION, (
                f"the pull request head moved from {snapshot.head_sha[:8]} to "
                f"{current_head[:8]} after the analysis"
            )

        analyzed = {
            source.file_id: (source.version, source.head_revision_id)
            for source in snapshot.evidence_corpus.sources
        }
        current = {
            entry["id"]: (
                str(entry["version"]) if entry.get("version") else None,
                entry.get("headRevisionId"),
            )
            for entry in self.drive.list_files()
        }
        for file_id, revision in analyzed.items():
            if file_id not in current:
                return FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION, (
                    f"an analyzed source is no longer in the folder: {file_id}"
                )
            if current[file_id] != revision:
                return FreshnessState.SOURCE_CHANGED_BEFORE_EXECUTION, (
                    f"a source was revised after the analysis: {file_id}"
                )

        return FreshnessState.FRESH, "head and source revisions are unchanged"

    # ------------------------------------------------------------- execution

    def build_plan(self, bundle: AnalysisBundle) -> ExecutionPlan:
        channel = getattr(self.slack_writer, "channel_id", "") if self.slack_writer else ""
        return build_execution_plan(
            decision=bundle.decision,
            case_id=bundle.case_id,
            analysis_id=bundle.analysis_id,
            slack_channel_id=channel,
            created_at=datetime.now(timezone.utc),
        )

    def execute(self, bundle: AnalysisBundle, plan: ExecutionPlan | None = None) -> ExecutionReceipt:
        """Carry out the plan, then read both providers back and verify."""
        started_at = datetime.now(timezone.utc)
        plan = plan or self.build_plan(bundle)
        snapshot = bundle.snapshot
        owner, repo = snapshot.repository.split("/")
        notes: list[str] = []

        freshness, freshness_reason = self.recheck_freshness(bundle)
        if freshness is not FreshnessState.FRESH:
            return self._seal(
                plan, bundle, started_at, ExecutionState.SUPERSEDED, freshness,
                github=None, slack=None,
                summary=f"no write was attempted. {freshness_reason}",
                notes=(freshness_reason,),
            )

        github_record = self._write_and_verify_github(plan, owner, repo)

        slack_record: EffectRecord | None = None
        if plan.requires_slack:
            slack_record = self._write_and_verify_slack(plan, bundle)
        else:
            notes.append(f"no Slack case: {plan.slack_effect.reason}")

        required = [r for r in (github_record, slack_record) if r is not None]
        if any(r.error for r in required):
            state = ExecutionState.FAILED
        elif all(r.matched for r in required):
            state = ExecutionState.VERIFIED
        else:
            state = ExecutionState.PARTIAL

        matched = sum(1 for r in required if r.matched)
        summary = (
            f"{matched} of {len(required)} required effect(s) were observed in "
            f"provider state with matching fields"
        )
        return self._seal(plan, bundle, started_at, state, freshness,
                          github=github_record, slack=slack_record,
                          summary=summary, notes=tuple(notes))

    def _write_and_verify_github(self, plan: ExecutionPlan, owner: str, repo: str) -> EffectRecord:
        effect = plan.github_effect
        target = f"{plan.repository}@{effect.target_sha}"
        if self.status_writer is None:
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target, error="no status writer is configured")

        intended = f"{effect.context}|{effect.state.value}|{effect.description}"
        try:
            created = self.status_writer.set_commit_status(
                owner, repo, effect.target_sha,
                state=effect.state.value, context=effect.context,
                description=effect.description,
            )
        except Exception as exc:  # noqa: BLE001
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target,
                                intended_payload_digest=payload_digest(intended),
                                error=f"{type(exc).__name__}: {exc}")

        observed = self.status_writer.latest_status_for_context(
            owner, repo, effect.target_sha, effect.context
        )
        mismatches: list[str] = []
        normalized: dict[str, str] = {}
        if observed is None:
            verification = VerificationState.NOT_FOUND
            mismatches.append(f"no status for context {effect.context!r} on that commit")
        else:
            normalized = {
                "repository": plan.repository,
                "sha": effect.target_sha,
                "context": str(observed.get("context")),
                "state": str(observed.get("state")),
                "description": str(observed.get("description") or ""),
            }
            if normalized["context"] != effect.context:
                mismatches.append(
                    f"context is {normalized['context']!r}, expected {effect.context!r}"
                )
            if normalized["state"] != effect.state.value:
                mismatches.append(
                    f"state is {normalized['state']!r}, expected {effect.state.value!r}"
                )
            if normalized["description"] != effect.description:
                mismatches.append("description does not match what was written")
            verification = (
                VerificationState.MATCHED if not mismatches else VerificationState.MISMATCHED
            )

        return EffectRecord(
            provider="github",
            action="set_commit_status",
            target=target,
            intended_payload_digest=payload_digest(intended),
            provider_resource_id=str(created.get("id")) if created else None,
            normalized_observed_payload=normalized,
            verification_state=verification,
            mismatches=tuple(mismatches),
        )

    def _write_and_verify_slack(self, plan: ExecutionPlan, bundle: AnalysisBundle) -> EffectRecord:
        target = f"{plan.slack_effect.channel_id}:{plan.case_id}"
        if self.slack_writer is None:
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, error="no Slack writer is configured")

        legal_names = {c.customer_id: c.legal_entity_name for c in self.registry.customers}
        text = render_case(bundle.decision, plan, legal_names=legal_names)
        wanted = expected_fields(bundle.decision, plan)

        try:
            existing = self.slack_writer.find_case_by_marker(plan.slack_effect.marker)
            if len(existing) > 1:
                return EffectRecord(
                    provider="slack", action="create_or_update_case", target=target,
                    intended_payload_digest=payload_digest(text),
                    error=(
                        f"{len(existing)} root cases already carry marker "
                        f"{plan.slack_effect.marker}. refusing to create another. "
                        f"a human must resolve the duplicate"
                    ),
                )
            if existing:
                ref = self.slack_writer.update_case(existing[0], text)
                action = "update_case"
            else:
                ref = self.slack_writer.post_case(text)
                action = "post_case"
        except Exception as exc:  # noqa: BLE001
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target,
                                intended_payload_digest=payload_digest(text),
                                error=f"{type(exc).__name__}: {exc}")

        try:
            observed = self.slack_writer.read_case(ref)
        except Exception as exc:  # noqa: BLE001
            return EffectRecord(provider="slack", action=action, target=target,
                                intended_payload_digest=payload_digest(text),
                                provider_resource_id=ref.resource_id,
                                verification_state=VerificationState.NOT_FOUND,
                                error=f"{type(exc).__name__}: {exc}")

        body = observed.get("text") or ""
        mismatches = [
            f"{name} missing from the case ({value!r})"
            for name, value in wanted.items()
            if value not in body
        ]
        normalized = {name: ("present" if value in body else "missing")
                      for name, value in wanted.items()}
        normalized["channel"] = ref.channel_id
        normalized["ts"] = ref.ts

        return EffectRecord(
            provider="slack",
            action=action,
            target=target,
            intended_payload_digest=payload_digest(text),
            provider_resource_id=ref.resource_id,
            normalized_observed_payload=normalized,
            verification_state=(
                VerificationState.MATCHED if not mismatches else VerificationState.MISMATCHED
            ),
            mismatches=tuple(mismatches),
        )

    def _seal(
        self, plan, bundle, started_at, state, freshness, *, github, slack, summary, notes,
    ) -> ExecutionReceipt:
        receipt = ExecutionReceipt(
            case_id=plan.case_id,
            analysis_id=plan.analysis_id,
            repository=plan.repository,
            pr_number=plan.pr_number,
            head_sha=plan.head_sha,
            corpus_digest=plan.corpus_digest,
            decision=plan.actual_decision,
            execution_state=state,
            freshness=freshness,
            github_effect=github,
            slack_effect=slack,
            verification_summary=summary,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            policy_versions={
                "policy_version": bundle.snapshot.policy_version,
                "parser_version": bundle.snapshot.parser_version,
                "decision_policy_version": DECISION_POLICY_VERSION,
                "execution_policy_version": EXECUTION_POLICY_VERSION,
                "semantic_model": bundle.analysis.metadata.model,
                "semantic_prompt_version": bundle.analysis.metadata.prompt_version,
                "semantic_schema_version": bundle.analysis.metadata.schema_version,
            },
            notes=notes,
        )
        return receipt


def save_receipt(receipt: ExecutionReceipt, directory: Path | None = None) -> Path:
    """Write the receipt under a git ignored runtime directory."""
    directory = Path(directory or RECEIPT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = receipt.completed_at.strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"receipt-{receipt.case_id}-{receipt.head_sha[:8]}-{stamp}.json"
    path.write_text(receipt.to_json())
    return path
