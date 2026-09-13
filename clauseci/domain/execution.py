"""
Bounded execution: plan, policy, and receipt.

Every write in this file is a deterministic consequence of a decision that was
already computed. No model is consulted here, and no model is ever handed a
write adapter.

Case identity deliberately excludes the head SHA. A new commit on the same pull
request belongs to the same engineering case, so the same Slack thread is
reused rather than a second one being created.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict

from clauseci.domain.decision import DecisionState
from clauseci.versions import (
    CASE_NAMESPACE,
    EXECUTION_POLICY_VERSION,
    RECEIPT_SCHEMA_VERSION,
    RETENTION_STATUS_CONTEXT,
)


class GithubStatusState(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


class SlackAction(str, Enum):
    NONE = "none"
    CREATE_OR_UPDATE_CASE = "create_or_update_case"


class ExecutionState(str, Enum):
    #: every required effect was observed in provider state and matched
    VERIFIED = "VERIFIED"
    #: some required effects succeeded and some definitively failed
    PARTIAL = "PARTIAL"
    #: at least one required effect may have happened and provider state cannot
    #: establish the outcome. This is never collapsed into FAILED, because the
    #: difference decides whether a retry is safe.
    UNKNOWN = "UNKNOWN"
    #: required effects definitively did not complete and nothing is uncertain
    FAILED = "FAILED"
    #: the head or the corpus moved, so this execution is no longer current
    SUPERSEDED = "SUPERSEDED"


class FreshnessState(str, Enum):
    FRESH = "FRESH"
    SUPERSEDED_BEFORE_EXECUTION = "SUPERSEDED_BEFORE_EXECUTION"
    SOURCE_CHANGED_BEFORE_EXECUTION = "SOURCE_CHANGED_BEFORE_EXECUTION"


class VerificationState(str, Enum):
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    NOT_FOUND = "NOT_FOUND"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------- identity

def build_case_id(owner: str, repo: str, pr_number: int) -> str:
    """
    Stable identity for one engineering case.

    Derived from the namespace, the repository and the pull request number.
    The head SHA is deliberately not part of it, so a new commit continues the
    same case instead of starting a new one.
    """
    seed = f"{CASE_NAMESPACE}|{owner}/{repo}|{pr_number}"
    return "cci-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def case_marker(case_id: str) -> str:
    """The machine readable marker embedded in the Slack case."""
    return f"clauseci-case:{case_id}"


def build_analysis_id(
    *,
    case_id: str,
    base_sha: str,
    head_sha: str,
    corpus_digest: str,
    policy_version: str,
    parser_version: str,
    semantic_model: str,
    semantic_prompt_version: str,
    decision_policy_version: str,
) -> str:
    """
    Identity of one analysis of one case.

    Unlike the case id this does move with the head SHA, the corpus and every
    version that could change the answer.
    """
    seed = "|".join([
        case_id, base_sha, head_sha, corpus_digest, policy_version,
        parser_version, semantic_model, semantic_prompt_version,
        decision_policy_version,
    ])
    return "an-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def payload_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- policy

class GithubEffectPlan(Frozen):
    context: str
    state: GithubStatusState
    description: str
    target_sha: str


class SlackEffectPlan(Frozen):
    action: SlackAction
    channel_id: str
    marker: str
    reason: str


#: Decision to external effect. Simple and documented on purpose.
#:
#:   PASS_SCOPED          github success, no Slack case
#:   CONFLICT             github failure, one Slack case
#:   REVIEW_REQUIRED      github failure, one Slack case
#:   NO_SUPPORTED_CHANGE  github success, no Slack case
#:
#: REVIEW_REQUIRED uses failure rather than error. Error reads as a broken
#: check, and this is a working check reporting that a human must look.
STATE_TO_GITHUB: dict[DecisionState, GithubStatusState] = {
    DecisionState.PASS_SCOPED: GithubStatusState.SUCCESS,
    DecisionState.CONFLICT: GithubStatusState.FAILURE,
    DecisionState.REVIEW_REQUIRED: GithubStatusState.FAILURE,
    DecisionState.NO_SUPPORTED_CHANGE: GithubStatusState.SUCCESS,
}

STATE_TO_SLACK: dict[DecisionState, SlackAction] = {
    DecisionState.PASS_SCOPED: SlackAction.NONE,
    DecisionState.CONFLICT: SlackAction.CREATE_OR_UPDATE_CASE,
    DecisionState.REVIEW_REQUIRED: SlackAction.CREATE_OR_UPDATE_CASE,
    DecisionState.NO_SUPPORTED_CHANGE: SlackAction.NONE,
}

#: GitHub truncates long descriptions, so keep them short and stable.
STATE_TO_DESCRIPTION: dict[DecisionState, str] = {
    DecisionState.PASS_SCOPED: "No customer retention conflict in the checked scope.",
    DecisionState.CONFLICT: "Customer retention conflict. See ClauseCI case.",
    DecisionState.REVIEW_REQUIRED: "Human review required. See ClauseCI case.",
    DecisionState.NO_SUPPORTED_CHANGE: "No supported retention surface changed.",
}

GITHUB_DESCRIPTION_LIMIT = 140


class ExecutionPlan(Frozen):
    """What will be written, decided before anything is written."""

    execution_policy_version: str = EXECUTION_POLICY_VERSION
    case_id: str
    analysis_id: str
    repository: str
    repository_id: int | None
    pr_number: int
    head_sha: str
    corpus_digest: str
    actual_decision: DecisionState
    github_effect: GithubEffectPlan
    slack_effect: SlackEffectPlan
    created_at: datetime

    @property
    def requires_slack(self) -> bool:
        return self.slack_effect.action is SlackAction.CREATE_OR_UPDATE_CASE


def build_execution_plan(
    *,
    decision,
    case_id: str,
    analysis_id: str,
    slack_channel_id: str,
    created_at: datetime,
) -> ExecutionPlan:
    """Turn a completed decision into a deterministic plan. No model involved."""
    state = decision.actual_head_state
    description = STATE_TO_DESCRIPTION[state][:GITHUB_DESCRIPTION_LIMIT]

    slack_action = STATE_TO_SLACK[state]
    if slack_action is SlackAction.NONE:
        slack_reason = f"{state.value} does not open an engineering case"
    else:
        slack_reason = f"{state.value} requires a human to see the evidence"

    return ExecutionPlan(
        case_id=case_id,
        analysis_id=analysis_id,
        repository=decision.binding.repository,
        repository_id=None,
        pr_number=decision.binding.pr_number,
        head_sha=decision.binding.head_sha,
        corpus_digest=decision.binding.corpus_digest,
        actual_decision=state,
        github_effect=GithubEffectPlan(
            context=RETENTION_STATUS_CONTEXT,
            state=STATE_TO_GITHUB[state],
            description=description,
            target_sha=decision.binding.head_sha,
        ),
        slack_effect=SlackEffectPlan(
            action=slack_action,
            channel_id=slack_channel_id,
            marker=case_marker(case_id),
            reason=slack_reason,
        ),
        created_at=created_at,
    )


# ---------------------------------------------------------------- receipt

class EffectRecord(Frozen):
    """One external effect, what was intended and what was observed."""

    provider: str
    action: str
    target: str
    intended_payload_digest: str | None = None
    provider_resource_id: str | None = None
    normalized_observed_payload: dict[str, str] = {}
    verification_state: VerificationState = VerificationState.NOT_ATTEMPTED
    mismatches: tuple[str, ...] = ()
    error: str | None = None
    #: durable journal identity and state for this effect
    effect_key: str | None = None
    journal_state: str | None = None
    reconciliation_outcome: str | None = None
    reconciliation_detail: str | None = None
    attempt_count: int = 0

    @property
    def matched(self) -> bool:
        return self.verification_state is VerificationState.MATCHED


class ExecutionReceipt(Frozen):
    """Evidence that the intended effects actually happened."""

    receipt_schema_version: str = RECEIPT_SCHEMA_VERSION
    case_id: str
    analysis_id: str
    repository: str
    pr_number: int
    head_sha: str
    corpus_digest: str
    decision: DecisionState
    execution_state: ExecutionState
    freshness: FreshnessState

    github_effect: EffectRecord | None = None
    slack_effect: EffectRecord | None = None

    verification_summary: str
    started_at: datetime
    completed_at: datetime

    policy_versions: dict[str, str]
    notes: tuple[str, ...] = ()
    #: freshness re-checked again immediately before sealing
    freshness_at_seal: FreshnessState | None = None
    #: set when effects were written and then the head or corpus moved
    superseded_after_effects: bool = False
    journal_path: str | None = None

    def binds(self, head_sha: str, corpus_digest: str) -> bool:
        """A receipt is only valid for the exact revision it was sealed on."""
        return self.head_sha == head_sha and self.corpus_digest == corpus_digest

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.model_dump(mode="json"), indent=indent, sort_keys=True)
