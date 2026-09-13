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
from clauseci.domain.case_message import (
    expected_fields,
    render_case,
    render_resolved_case,
    resolved_expected_fields,
)
from clauseci.domain.decision import DecisionState
from clauseci.domain.digests import canonical_json
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
from clauseci.domain.effects import EffectState, build_effect_key
from clauseci.domain.models import AnalysisSnapshot
from clauseci.domain.obligations import ObligationAnalysis
from clauseci.faults import (
    NO_FAULTS,
    FaultPoint,
    InjectedFailure,
    InjectedResponseLoss,
)
from clauseci.journal import CaseState, Journal, LockNotAcquired
from clauseci.reconcile import (
    ReconciliationOutcome,
    reconcile_github_status,
    reconcile_slack_case,
    reconcile_unfinished_effects,
)
from clauseci.settings import ROOT, SUPPORTED_RETENTION_PATHS
from clauseci.versions import (
    CASE_NAMESPACE,
    DECISION_POLICY_VERSION,
    EXECUTION_POLICY_VERSION,
    RECONCILIATION_POLICY_VERSION,
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
        journal: Journal | None = None,
        faults=NO_FAULTS,
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
        self.journal = journal
        self.faults = faults
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
        """
        Build the plan, taking the existing case state into account.

        Whether a pass stays quiet or closes a case depends on whether a case is
        already open, so the journal is consulted before the plan is fixed.
        """
        channel = getattr(self.slack_writer, "channel_id", "") if self.slack_writer else ""
        journal = self.journal or Journal()
        existing = journal.get_case(bundle.case_id)
        return build_execution_plan(
            decision=bundle.decision,
            case_id=bundle.case_id,
            analysis_id=bundle.analysis_id,
            slack_channel_id=channel,
            created_at=datetime.now(timezone.utc),
            case_is_open=bool(existing and existing.is_open),
        )

    def execute(self, bundle: AnalysisBundle, plan: ExecutionPlan | None = None) -> ExecutionReceipt:
        """
        Carry out the plan under a local case lock, recording intent first.

        Order: lock, record case and analysis, freshness, reconcile anything
        left unfinished by an earlier run, GitHub effect, freshness again,
        Slack effect, freshness once more, then seal.
        """
        started_at = datetime.now(timezone.utc)
        plan = plan or self.build_plan(bundle)
        snapshot = bundle.snapshot
        owner, repo = snapshot.repository.split("/")
        notes: list[str] = []

        journal = self.journal or Journal()
        # the case exists whoever ends up executing, and a receipt cannot be
        # recorded against a case row that is not there
        journal.upsert_case(
            case_id=plan.case_id, namespace=CASE_NAMESPACE,
            repository_full_name=snapshot.repository,
            repository_id=snapshot.repository_id, pr_number=snapshot.pr_number,
        )
        try:
            with journal.case_lock(plan.case_id):
                return self._execute_locked(
                    journal, bundle, plan, owner, repo, started_at, notes
                )
        except LockNotAcquired as exc:
            notes.append(str(exc))
            return self._seal(
                plan, bundle, started_at, ExecutionState.FAILED, FreshnessState.FRESH,
                github=None, slack=None,
                summary="another local execution owns this case, so nothing was written",
                notes=tuple(notes), journal=journal,
            )

    def _execute_locked(self, journal, bundle, plan, owner, repo, started_at, notes):
        snapshot = bundle.snapshot
        analysis = bundle.analysis

        journal.upsert_case(
            case_id=plan.case_id, namespace=CASE_NAMESPACE,
            repository_full_name=snapshot.repository,
            repository_id=snapshot.repository_id, pr_number=snapshot.pr_number,
        )
        journal.record_analysis(
            analysis_id=plan.analysis_id, case_id=plan.case_id,
            base_sha=snapshot.base_sha, head_sha=snapshot.head_sha,
            corpus_digest=snapshot.corpus_digest,
            snapshot_schema_version=snapshot.snapshot_schema_version,
            parser_version=snapshot.parser_version,
            policy_version=snapshot.policy_version,
            semantic_model=analysis.metadata.model,
            semantic_prompt_version=analysis.metadata.prompt_version,
            semantic_schema_version=analysis.metadata.schema_version,
            decision_policy_version=DECISION_POLICY_VERSION,
            decision=plan.actual_decision.value,
        )

        # boundary one: nothing has been written yet
        freshness, reason = self.recheck_freshness(bundle)
        freshness = self.faults.override_freshness(FaultPoint.FRESHNESS_BEFORE_EFFECTS, freshness)
        if freshness is not FreshnessState.FRESH:
            for effect in journal.unfinished_effects(plan.case_id):
                if effect.state is EffectState.PLANNED:
                    journal.transition_effect(effect.effect_key, EffectState.SUPERSEDED,
                                              reconciliation_state="stale before any write")
            return self._seal(
                plan, bundle, started_at, ExecutionState.SUPERSEDED, freshness,
                github=None, slack=None,
                summary=f"no write was attempted. {reason}",
                notes=(reason,), journal=journal,
            )

        recovered = reconcile_unfinished_effects(
            journal, slack_writer=self.slack_writer,
            status_writer=self.status_writer, case_id=plan.case_id,
        )
        for effect, result in recovered:
            notes.append(
                f"recovered {effect.provider} effect {effect.effect_key}: "
                f"{result.outcome.value}. {result.detail}"
            )

        github_record = self._do_github_effect(journal, plan, owner, repo)

        slack_record = None
        superseded_after = False
        if plan.requires_slack:
            mid, mid_reason = self.recheck_freshness(bundle)
            mid = self.faults.override_freshness(FaultPoint.FRESHNESS_BETWEEN_EFFECTS, mid)
            if mid is not FreshnessState.FRESH:
                superseded_after = True
                notes.append(
                    f"stopped before the Slack case: {mid_reason}. the GitHub effect "
                    f"stays recorded and the analyzed sha is unchanged in the audit"
                )
            else:
                slack_record = self._do_slack_effect(journal, plan, bundle)
        else:
            notes.append(f"no Slack case: {plan.slack_effect.reason}")

        # boundary three: effects are written, confirm they are still current
        at_seal, seal_reason = self.recheck_freshness(bundle)
        at_seal = self.faults.override_freshness(FaultPoint.FRESHNESS_BEFORE_SEAL, at_seal)
        if at_seal is not FreshnessState.FRESH:
            superseded_after = True
            notes.append(
                f"the effects were written and then state moved: {seal_reason}. "
                f"this execution is no longer current"
            )

        required = [r for r in (github_record, slack_record) if r is not None]
        missing_required = plan.requires_slack and slack_record is None

        if superseded_after:
            state = ExecutionState.SUPERSEDED
        elif any(r.journal_state == EffectState.UNKNOWN.value for r in required):
            state = ExecutionState.UNKNOWN
        elif missing_required:
            state = ExecutionState.PARTIAL
        elif any(r.error for r in required):
            state = ExecutionState.FAILED
        elif required and all(r.matched for r in required):
            state = ExecutionState.VERIFIED
        else:
            state = ExecutionState.PARTIAL

        matched = sum(1 for r in required if r.matched)
        summary = (
            f"{matched} of {len(required) + (1 if missing_required else 0)} required "
            f"effect(s) were observed in provider state with matching fields"
        )
        return self._seal(plan, bundle, started_at, state, freshness,
                          github=github_record, slack=slack_record,
                          summary=summary, notes=tuple(notes), journal=journal,
                          freshness_at_seal=at_seal,
                          superseded_after_effects=superseded_after)

    # --------------------------------------------------------- one effect

    def _do_github_effect(self, journal, plan: ExecutionPlan, owner: str, repo: str):
        effect = plan.github_effect
        target = f"{plan.repository}@{effect.target_sha}"
        payload = {
            "repository": plan.repository, "sha": effect.target_sha,
            "context": effect.context, "state": effect.state.value,
            "description": effect.description,
        }
        digest = payload_digest(canonical_json(payload))
        key = build_effect_key(
            case_id=plan.case_id, analysis_id=plan.analysis_id, provider="github",
            action_type="set_commit_status", target=target,
            intended_payload_digest=digest,
        )

        if self.status_writer is None:
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target, effect_key=key,
                                error="no status writer is configured")

        # intent is durable before the provider is contacted
        row = journal.plan_effect(
            effect_key=key, case_id=plan.case_id, analysis_id=plan.analysis_id,
            provider="github", action_type="set_commit_status", target=target,
            intended_payload=payload, intended_payload_digest=digest,
        )

        if row.state is EffectState.VERIFIED:
            return EffectRecord(
                provider="github", action="already_verified", target=target,
                intended_payload_digest=digest, effect_key=key,
                provider_resource_id=row.provider_resource_id,
                journal_state=row.state.value,
                verification_state=VerificationState.MATCHED,
                reconciliation_outcome=ReconciliationOutcome.ADOPTED.value,
                reconciliation_detail="this exact intent was already verified",
                attempt_count=row.attempt_count,
            )

        if row.state in (EffectState.FAILED, EffectState.SUPERSEDED):
            return EffectRecord(
                provider="github", action="already_closed", target=target,
                intended_payload_digest=digest, effect_key=key,
                journal_state=row.state.value,
                error=row.last_error or f"effect is already {row.state.value}",
                attempt_count=row.attempt_count,
            )

        # commit statuses are history bearing. if the latest record in this
        # context already says what this analysis intends, adopt it rather than
        # append another identical one.
        existing = reconcile_github_status(
            status_writer=self.status_writer, owner=owner, repo=repo,
            sha=effect.target_sha, context=effect.context,
            intended_state=effect.state.value,
            intended_description=effect.description,
        )
        if existing.outcome is ReconciliationOutcome.ADOPTED:
            journal.transition_effect(key, EffectState.IN_FLIGHT)
            row = journal.transition_effect(
                key, EffectState.VERIFIED,
                provider_resource_id=existing.provider_resource_id,
                reconciliation_state=existing.outcome.value,
            )
            return EffectRecord(
                provider="github", action="adopt_existing_status", target=target,
                intended_payload_digest=digest, effect_key=key,
                provider_resource_id=existing.provider_resource_id,
                normalized_observed_payload={**payload},
                verification_state=VerificationState.MATCHED,
                journal_state=row.state.value,
                reconciliation_outcome=existing.outcome.value,
                reconciliation_detail=existing.detail,
                attempt_count=row.attempt_count,
            )

        journal.transition_effect(key, EffectState.IN_FLIGHT, bump_attempt=True)
        try:
            self.faults.check(FaultPoint.BEFORE_PROVIDER_CALL,
                              provider="github", action="set_commit_status")
            created = self.status_writer.set_commit_status(
                owner, repo, effect.target_sha, state=effect.state.value,
                context=effect.context, description=effect.description,
            )
            self.faults.check(FaultPoint.AFTER_PROVIDER_RESPONSE,
                              provider="github", action="set_commit_status")
        except InjectedFailure as exc:
            row = journal.transition_effect(key, EffectState.FAILED, last_error=str(exc))
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=row.state.value,
                                error=str(exc), attempt_count=row.attempt_count)
        except InjectedResponseLoss as exc:
            row = journal.transition_effect(key, EffectState.UNKNOWN, last_error=str(exc),
                                            reconciliation_state="response lost")
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=row.state.value,
                                verification_state=VerificationState.NOT_ATTEMPTED,
                                error=str(exc), attempt_count=row.attempt_count)
        except Exception as exc:  # noqa: BLE001
            row = journal.transition_effect(key, EffectState.UNKNOWN,
                                            last_error=f"{type(exc).__name__}: {exc}",
                                            reconciliation_state="call raised")
            return EffectRecord(provider="github", action="set_commit_status",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=row.state.value,
                                error=f"{type(exc).__name__}: {exc}",
                                attempt_count=row.attempt_count)

        observed = self.status_writer.latest_status_for_context(
            owner, repo, effect.target_sha, effect.context
        )
        observed = self.faults.mutate_readback("github", observed)

        mismatches: list[str] = []
        normalized: dict[str, str] = {}
        if observed is None:
            verification = VerificationState.NOT_FOUND
            mismatches.append(f"no status for context {effect.context!r} on that commit")
        else:
            normalized = {
                "repository": plan.repository, "sha": effect.target_sha,
                "context": str(observed.get("context")),
                "state": str(observed.get("state")),
                "description": str(observed.get("description") or ""),
            }
            if normalized["context"] != effect.context:
                mismatches.append(f"context is {normalized['context']!r}")
            if normalized["state"] != effect.state.value:
                mismatches.append(
                    f"state is {normalized['state']!r}, expected {effect.state.value!r}")
            if normalized["description"] != effect.description:
                mismatches.append("description does not match what was written")
            verification = (VerificationState.MATCHED if not mismatches
                            else VerificationState.MISMATCHED)

        row = journal.transition_effect(
            key, EffectState.VERIFIED if verification is VerificationState.MATCHED
            else EffectState.UNKNOWN,
            provider_resource_id=str(created.get("id")) if created else None,
            last_error="; ".join(mismatches) or None,
            reconciliation_state="direct read back",
        )
        return EffectRecord(
            provider="github", action="set_commit_status", target=target,
            intended_payload_digest=digest, effect_key=key,
            provider_resource_id=str(created.get("id")) if created else None,
            normalized_observed_payload=normalized,
            verification_state=verification, mismatches=tuple(mismatches),
            journal_state=row.state.value, attempt_count=row.attempt_count,
        )

    def _do_slack_effect(self, journal, plan: ExecutionPlan, bundle: AnalysisBundle):
        target = f"{plan.slack_effect.channel_id}:{plan.case_id}"
        if self.slack_writer is None:
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, error="no Slack writer is configured")

        legal_names = {c.customer_id: c.legal_entity_name for c in self.registry.customers}
        previous = self._previous_conflicting_analysis(journal, plan)
        if plan.is_resolution:
            text = render_resolved_case(
                bundle.decision, plan, legal_names=legal_names,
                previous_head_sha=previous["head_sha"],
                previous_decision=previous["decision"],
            )
            wanted = resolved_expected_fields(
                bundle.decision, plan, previous_head_sha=previous["head_sha"])
        else:
            text = render_case(bundle.decision, plan, legal_names=legal_names)
            wanted = expected_fields(bundle.decision, plan)
        marker = plan.slack_effect.marker
        digest = payload_digest(text)
        payload = {"marker": marker, "channel": plan.slack_effect.channel_id,
                   "expected_values": wanted, "text_digest": digest}
        key = build_effect_key(
            case_id=plan.case_id, analysis_id=plan.analysis_id, provider="slack",
            action_type="create_or_update_case", target=target,
            intended_payload_digest=digest,
        )

        row = journal.plan_effect(
            effect_key=key, case_id=plan.case_id, analysis_id=plan.analysis_id,
            provider="slack", action_type="create_or_update_case", target=target,
            intended_payload=payload, intended_payload_digest=digest,
        )

        if row.state is EffectState.VERIFIED:
            # this exact intent was already carried out and confirmed. writing
            # again would add nothing and could disturb a case a person is reading.
            return EffectRecord(
                provider="slack", action="already_verified", target=target,
                intended_payload_digest=digest, effect_key=key,
                provider_resource_id=row.provider_resource_id,
                journal_state=row.state.value,
                verification_state=VerificationState.MATCHED,
                reconciliation_outcome=ReconciliationOutcome.ADOPTED.value,
                reconciliation_detail="this exact intent was already verified",
                attempt_count=row.attempt_count,
            )
        if row.state in (EffectState.FAILED, EffectState.SUPERSEDED):
            return EffectRecord(
                provider="slack", action="already_closed", target=target,
                intended_payload_digest=digest, effect_key=key,
                journal_state=row.state.value,
                error=row.last_error or f"effect is already {row.state.value}",
                attempt_count=row.attempt_count,
            )

        case = journal.get_case(plan.case_id)
        known = case.slack_resource_id if case else None

        # never create before establishing whether the case already exists
        found = reconcile_slack_case(
            slack_writer=self.slack_writer, marker=marker,
            known_resource_id=known, expected_values=wanted,
        )

        if found.outcome is ReconciliationOutcome.AMBIGUOUS:
            updated = journal.transition_effect(
                key, EffectState.FAILED, last_error=found.detail,
                reconciliation_state=found.outcome.value)
            return EffectRecord(provider="slack", action="refused_ambiguous_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=updated.state.value,
                                reconciliation_outcome=found.outcome.value,
                                reconciliation_detail=found.detail,
                                error=found.detail, attempt_count=updated.attempt_count)

        if found.outcome is ReconciliationOutcome.UNREACHABLE:
            # the provider could not be inspected and nothing has been written.
            # the effect stays PLANNED if it never left planning, because an
            # untried effect is not an uncertain one.
            if row.state is not EffectState.PLANNED:
                row = journal.transition_effect(
                    key, EffectState.UNKNOWN, last_error=found.detail,
                    reconciliation_state=found.outcome.value)
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=row.state.value,
                                reconciliation_outcome=found.outcome.value,
                                reconciliation_detail=found.detail,
                                error=found.detail, attempt_count=row.attempt_count)

        from clauseci.adapters.slack_write import SlackMessageRef

        existing_ref = None
        if found.outcome is ReconciliationOutcome.ADOPTED and found.provider_resource_id:
            channel, _, ts = found.provider_resource_id.partition("/")
            existing_ref = SlackMessageRef(channel, ts)

            # do not overwrite content this system did not write
            try:
                current = self.slack_writer.read_case(existing_ref)
            except Exception as exc:  # noqa: BLE001
                current = None
            if current is not None:
                current_digest = payload_digest(current.get("text") or "")
                last_known = case.slack_payload_digest if case else None
                if (last_known is not None and current_digest != last_known
                        and current_digest != digest):
                    detail = (
                        "the Slack case has been edited since ClauseCI last wrote it. "
                        "refusing to overwrite content this system did not write"
                    )
                    updated = journal.transition_effect(
                        key, EffectState.FAILED, last_error=detail,
                        reconciliation_state=ReconciliationOutcome.FOREIGN_CONTENT.value)
                    return EffectRecord(
                        provider="slack", action="refused_foreign_content", target=target,
                        intended_payload_digest=digest, effect_key=key,
                        provider_resource_id=found.provider_resource_id,
                        journal_state=updated.state.value,
                        reconciliation_outcome=ReconciliationOutcome.FOREIGN_CONTENT.value,
                        reconciliation_detail=detail, error=detail,
                        attempt_count=updated.attempt_count)

        if plan.is_resolution and existing_ref is None:
            detail = (
                "a resolution must update the existing case, and no existing case "
                "could be found. refusing to open a new root case for a pass"
            )
            closed = journal.transition_effect(
                key, EffectState.FAILED, last_error=detail,
                reconciliation_state=found.outcome.value)
            return EffectRecord(provider="slack", action="refused_missing_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=closed.state.value,
                                reconciliation_outcome=found.outcome.value,
                                reconciliation_detail=detail, error=detail,
                                attempt_count=closed.attempt_count)

        journal.transition_effect(key, EffectState.IN_FLIGHT, bump_attempt=True)
        try:
            self.faults.check(FaultPoint.BEFORE_PROVIDER_CALL,
                              provider="slack", action="create_or_update_case")
            if existing_ref is not None:
                ref = self.slack_writer.update_case(existing_ref, text)
                action = "update_case"
            else:
                ref = self.slack_writer.post_case(text)
                action = "post_case"
            self.faults.check(FaultPoint.AFTER_PROVIDER_RESPONSE,
                              provider="slack", action="create_or_update_case")
        except InjectedFailure as exc:
            updated = journal.transition_effect(key, EffectState.FAILED, last_error=str(exc))
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=updated.state.value,
                                error=str(exc), attempt_count=updated.attempt_count)
        except InjectedResponseLoss as exc:
            updated = journal.transition_effect(
                key, EffectState.UNKNOWN, last_error=str(exc),
                reconciliation_state="response lost after a real write")
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=updated.state.value,
                                error=str(exc), attempt_count=updated.attempt_count)
        except Exception as exc:  # noqa: BLE001
            updated = journal.transition_effect(
                key, EffectState.UNKNOWN, last_error=f"{type(exc).__name__}: {exc}",
                reconciliation_state="call raised")
            return EffectRecord(provider="slack", action="create_or_update_case",
                                target=target, intended_payload_digest=digest,
                                effect_key=key, journal_state=updated.state.value,
                                error=f"{type(exc).__name__}: {exc}",
                                attempt_count=updated.attempt_count)

        try:
            observed = self.slack_writer.read_case(ref)
        except Exception as exc:  # noqa: BLE001
            updated = journal.transition_effect(
                key, EffectState.UNKNOWN, provider_resource_id=ref.resource_id,
                last_error=f"{type(exc).__name__}: {exc}",
                reconciliation_state="read back failed")
            return EffectRecord(provider="slack", action=action, target=target,
                                intended_payload_digest=digest, effect_key=key,
                                provider_resource_id=ref.resource_id,
                                journal_state=updated.state.value,
                                verification_state=VerificationState.NOT_FOUND,
                                error=f"{type(exc).__name__}: {exc}",
                                attempt_count=updated.attempt_count)

        observed = self.faults.mutate_readback("slack", observed)
        body = observed.get("text") or ""
        mismatches = [f"{name} missing from the case ({value!r})"
                      for name, value in wanted.items() if value not in body]
        normalized = {name: ("present" if value in body else "missing")
                      for name, value in wanted.items()}
        normalized["channel"] = ref.channel_id
        normalized["ts"] = ref.ts

        verification = (VerificationState.MATCHED if not mismatches
                        else VerificationState.MISMATCHED)
        updated = journal.transition_effect(
            key, EffectState.VERIFIED if not mismatches else EffectState.UNKNOWN,
            provider_resource_id=ref.resource_id,
            last_error="; ".join(mismatches) or None,
            reconciliation_state="direct read back")
        journal.set_case_slack_resource(
            plan.case_id, ref.resource_id, payload_digest(body))

        if plan.is_resolution and not mismatches:
            # the case only closes once the closure itself has been read back
            journal.set_case_state(
                plan.case_id, CaseState.RESOLVED,
                resolved_by_analysis_id=plan.analysis_id,
                resolved_head_sha=plan.head_sha,
            )
        elif plan.slack_effect.action is SlackAction.CREATE_OR_UPDATE_CASE and not mismatches:
            journal.set_case_state(plan.case_id, CaseState.OPEN)

        return EffectRecord(
            provider="slack", action="resolve_case" if plan.is_resolution else action,
            target=target,
            intended_payload_digest=digest, effect_key=key,
            provider_resource_id=ref.resource_id,
            normalized_observed_payload=normalized,
            verification_state=verification, mismatches=tuple(mismatches),
            journal_state=updated.state.value,
            reconciliation_outcome=found.outcome.value,
            reconciliation_detail=found.detail,
            attempt_count=updated.attempt_count)

    def _previous_conflicting_analysis(self, journal, plan) -> dict:
        """The earlier analysis this resolution is closing, for the audit trail."""
        for row in reversed(journal.analyses_for_case(plan.case_id)):
            if row["head_sha"] != plan.head_sha and row["decision"] != "PASS_SCOPED":
                return {"head_sha": row["head_sha"], "decision": row["decision"],
                        "analysis_id": row["analysis_id"]}
        return {"head_sha": plan.head_sha, "decision": "unknown", "analysis_id": ""}

    def _seal(
        self, plan, bundle, started_at, state, freshness, *, github, slack, summary,
        notes, journal=None, freshness_at_seal=None, superseded_after_effects=False,
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
                "reconciliation_policy_version": RECONCILIATION_POLICY_VERSION,
            },
            notes=notes,
            freshness_at_seal=freshness_at_seal,
            superseded_after_effects=superseded_after_effects,
            journal_path=str(journal.path) if journal is not None else None,
        )
        if journal is not None:
            journal.record_receipt(
                case_id=receipt.case_id, analysis_id=receipt.analysis_id,
                head_sha=receipt.head_sha, corpus_digest=receipt.corpus_digest,
                execution_state=receipt.execution_state.value,
                receipt_json=receipt.to_json(),
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
