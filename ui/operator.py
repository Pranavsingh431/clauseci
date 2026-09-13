"""
Local live operator for the recorded demo.

This is a thin adapter over the existing authenticated workflow. It builds no
decision, ranks no candidate, writes nothing itself and knows nothing about
GitHub or Slack. It calls the same three supported methods the product command
calls, in the same order:

    Workflow.run_analysis  ->  Workflow.build_plan  ->  Workflow.execute

Gating. Nothing here runs, and no provider module is imported, unless
CLAUSECI_OPERATOR_MODE is set to 1 in the local environment. Every provider
import is deliberately inside a function rather than at module scope, so
importing this module on a public deployment pulls in no adapter, needs no
credential and offers no control. The deployed public console must stay read
only and credential free, and the way that is enforced is that the code which
could write is never loaded there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

OPERATOR_MODE_VAR = "CLAUSECI_OPERATOR_MODE"

#: The named stages a viewer sees, grouped under the real phase that confirms
#: them. A stage is only ever reported once the phase actually returned, so
#: nothing here is a timed animation.
READ_AND_DECIDE = (
    "Reading GitHub pull request",
    "Resolving effective configuration",
    "Reading customer agreements from Google Drive",
    "Interpreting represented obligations",
    "Evaluating release decision",
    "Building supported correction candidates",
)
WRITE_AND_VERIFY = (
    "Publishing GitHub release status",
    "Creating or updating Slack engineering case",
    "Reading provider state back",
    "Sealing verified execution receipt",
)


def enabled() -> bool:
    """True only when the local environment explicitly asks for the operator."""
    return os.environ.get(OPERATOR_MODE_VAR, "").strip() == "1"


class OperatorError(RuntimeError):
    """Something the person at the keyboard should read, not a traceback."""


@dataclass
class RunResult:
    """What one authenticated run produced, in display terms."""

    repository: str
    pr_number: int
    head_sha: str
    decision: str
    decision_reason: str
    execution_state: str
    freshness: str
    verification_summary: str
    github_state: str | None = None
    github_context: str | None = None
    github_verification: str | None = None
    slack_action: str | None = None
    slack_verification: str | None = None
    slack_case_open: bool | None = None
    preferred_candidate: str | None = None
    preferred_description: str | None = None
    preferred_values: dict[str, dict[str, int | None]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    receipt_path: str | None = None


def parse_pr(raw: str) -> str:
    """Accept what the product command accepts, and refuse the rest clearly."""
    reference = (raw or "").strip()
    if not reference:
        raise OperatorError("Enter a pull request number before running.")
    if reference.isdigit():
        if int(reference) <= 0:
            raise OperatorError("A pull request number starts at 1.")
        return reference
    if reference.startswith("http") or "#" in reference:
        return reference
    raise OperatorError(
        f"{reference!r} is not a pull request. Use a number such as 7, an "
        "owner/repo#7 reference, or the full pull request URL."
    )


def build_workflow() -> Any:
    """
    Wire the same workflow the product command wires.

    Every import is local. On a public deployment this function is never
    called, so no adapter, no credential and no provider SDK is ever loaded.
    """
    from clauseci.adapters.drive import DriveReader
    from clauseci.adapters.github import GitHubReader
    from clauseci.adapters.github_write import GitHubStatusWriter
    from clauseci.adapters.slack_write import SlackCaseWriter
    from clauseci.domain.evidence_text import DriveBytesSource, EvidenceTextProvider
    from clauseci.domain.semantic_cache import SemanticCache
    from clauseci.registry import load_registry
    from clauseci.settings import load_settings
    from clauseci.workflow import Workflow

    settings = load_settings()
    channel = os.environ.get("SLACK_ALERT_CHANNEL_ID", "").strip()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not channel or not token:
        raise OperatorError(
            "SLACK_ALERT_CHANNEL_ID and SLACK_BOT_TOKEN must both be set in this "
            "shell before an authenticated run can publish anything."
        )

    drive = DriveReader(settings.google_token_path, settings.drive_folder_id)
    return Workflow(
        settings=settings,
        registry=load_registry(),
        github_reader=GitHubReader(settings),
        drive=drive,
        text_provider=EvidenceTextProvider(DriveBytesSource(drive)),
        status_writer=GitHubStatusWriter(settings),
        slack_writer=SlackCaseWriter(token, channel),
        cache=SemanticCache(enabled=True),
    )


def run(pr_reference: str, on_phase: Callable[[str, str], None] | None = None) -> RunResult:
    """
    Run the authenticated workflow once.

    `on_phase` is called with (phase, state) as each real phase starts and
    finishes, so the caller can show progress that corresponds to work that
    actually happened.
    """
    if not enabled():
        raise OperatorError(
            f"The live operator is off. Set {OPERATOR_MODE_VAR}=1 locally to enable it."
        )

    from clauseci.workflow import WorkflowError, save_receipt

    reference = parse_pr(pr_reference)
    notify = on_phase or (lambda phase, state: None)
    workflow = build_workflow()

    try:
        notify("analysis", "running")
        bundle = workflow.run_analysis(reference)
        notify("analysis", "done")

        notify("plan", "running")
        plan = workflow.build_plan(bundle)
        notify("plan", "done")

        notify("execute", "running")
        receipt = workflow.execute(bundle, plan)
        notify("execute", "done")
    except WorkflowError as exc:
        raise OperatorError(str(exc)) from exc

    decision = bundle.decision
    preferred = next(
        (c for c in decision.candidates if c.candidate_id == decision.preferred_candidate_id),
        None,
    )
    values: dict[str, dict[str, int | None]] = {}
    if preferred is not None:
        for value in preferred.effective_values:
            values.setdefault(value.customer_id, {})[value.category] = value.value

    result = RunResult(
        repository=receipt.repository,
        pr_number=receipt.pr_number,
        head_sha=receipt.head_sha,
        decision=receipt.decision.value,
        decision_reason=decision.actual_head_reason,
        execution_state=receipt.execution_state.value,
        freshness=receipt.freshness.value,
        verification_summary=receipt.verification_summary,
        preferred_candidate=preferred.candidate_id if preferred else None,
        preferred_description=preferred.description if preferred else None,
        preferred_values=values,
        notes=receipt.notes,
    )
    if receipt.github_effect is not None:
        result.github_state = plan.github_effect.state.value
        result.github_context = plan.github_effect.context
        result.github_verification = receipt.github_effect.verification_state.value
    if receipt.slack_effect is not None:
        result.slack_action = plan.slack_effect.action.value
        result.slack_verification = receipt.slack_effect.verification_state.value

    try:
        result.receipt_path = str(save_receipt(receipt))
    except Exception:  # noqa: BLE001 - a saved copy is a convenience, not the result
        result.receipt_path = None
    return result
