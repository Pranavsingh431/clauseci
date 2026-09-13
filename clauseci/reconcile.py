"""
Reconciling uncertain effects against provider state.

The rule this file exists to enforce: when a prior outcome cannot be
established locally, go and look at the provider. Never retry blindly.

Slack and GitHub need different treatment. A Slack case is one resource that
must stay unique. A GitHub commit status is history bearing, and several
records in one context are normal, so uniqueness is not the thing to enforce
there. The latest state is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from clauseci.domain.effects import EffectState
from clauseci.journal import EffectRow, Journal


class ReconciliationOutcome(str, Enum):
    #: provider state matched the intent and the effect was adopted
    ADOPTED = "ADOPTED"
    #: provider state proves the effect did not happen
    ABSENT = "ABSENT"
    #: more than one candidate resource carries the marker
    AMBIGUOUS = "AMBIGUOUS"
    #: provider state exists but does not match the intent
    MISMATCHED = "MISMATCHED"
    #: the provider could not be inspected
    UNREACHABLE = "UNREACHABLE"
    #: provider state shows content this system did not write
    FOREIGN_CONTENT = "FOREIGN_CONTENT"


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    detail: str
    provider_resource_id: str | None = None
    observed_payload_digest: str | None = None


def reconcile_slack_case(
    *,
    slack_writer,
    marker: str,
    known_resource_id: str | None,
    expected_values: dict[str, str],
) -> ReconciliationResult:
    """
    Establish whether the Slack case already exists, and which resource it is.

    Order matters. The stored resource id is the normal path, because it is an
    exact lookup. The channel history search by marker is recovery, used when
    the id was never recorded because a response was lost.
    """
    from clauseci.adapters.slack_write import SlackMessageRef

    if known_resource_id:
        channel, _, ts = known_resource_id.partition("/")
        ref = SlackMessageRef(channel, ts)
        try:
            observed = slack_writer.read_case(ref)
        except Exception as exc:  # noqa: BLE001
            return ReconciliationResult(
                ReconciliationOutcome.UNREACHABLE,
                f"the stored Slack resource could not be read: {exc}",
            )
        body = observed.get("text") or ""
        if marker not in body:
            return ReconciliationResult(
                ReconciliationOutcome.MISMATCHED,
                f"the stored resource {known_resource_id} no longer carries {marker}",
                provider_resource_id=known_resource_id,
            )
        return ReconciliationResult(
            ReconciliationOutcome.ADOPTED,
            f"found the case at its recorded resource id, no history search needed",
            provider_resource_id=known_resource_id,
        )

    try:
        found = slack_writer.find_case_by_marker(marker)
    except Exception as exc:  # noqa: BLE001
        return ReconciliationResult(
            ReconciliationOutcome.UNREACHABLE,
            f"the channel could not be searched: {exc}",
        )

    if len(found) > 1:
        return ReconciliationResult(
            ReconciliationOutcome.AMBIGUOUS,
            f"{len(found)} root cases carry {marker}. refusing to create another. "
            f"a person must resolve the duplicate",
        )
    if not found:
        return ReconciliationResult(
            ReconciliationOutcome.ABSENT,
            f"no root case carries {marker}, so the earlier write did not land",
        )

    ref = found[0]
    return ReconciliationResult(
        ReconciliationOutcome.ADOPTED,
        f"recovered the case by marker search and adopted {ref.resource_id}",
        provider_resource_id=ref.resource_id,
    )


def reconcile_github_status(
    *,
    status_writer,
    owner: str,
    repo: str,
    sha: str,
    context: str,
    intended_state: str,
    intended_description: str,
) -> ReconciliationResult:
    """
    Decide whether the intended status is already the latest one.

    Commit statuses are history bearing. Several records in the same context are
    normal and are not duplicates. What matters is whether the latest record for
    this context already says what this analysis intends. If it does, the effect
    is adopted rather than written again, so a restart loop cannot append an
    endless column of identical statuses.

    A deliberate re-execution still appends a new record, because that is a new
    intent rather than a recovery.
    """
    try:
        latest = status_writer.latest_status_for_context(owner, repo, sha, context)
    except Exception as exc:  # noqa: BLE001
        return ReconciliationResult(
            ReconciliationOutcome.UNREACHABLE,
            f"commit statuses could not be read: {exc}",
        )

    if latest is None:
        return ReconciliationResult(
            ReconciliationOutcome.ABSENT,
            f"no status exists for context {context!r} on {sha[:8]}",
        )
    if latest.get("context") != context:
        return ReconciliationResult(
            ReconciliationOutcome.MISMATCHED,
            f"the latest status is context {latest.get('context')!r}, not {context!r}",
        )
    if latest.get("state") != intended_state or \
            (latest.get("description") or "") != intended_description:
        return ReconciliationResult(
            ReconciliationOutcome.MISMATCHED,
            f"the latest status says {latest.get('state')!r}, this analysis intends "
            f"{intended_state!r}",
            provider_resource_id=str(latest.get("id")),
        )
    return ReconciliationResult(
        ReconciliationOutcome.ADOPTED,
        f"the latest status in {context!r} already equals the intended state",
        provider_resource_id=str(latest.get("id")),
    )


def reconcile_unfinished_effects(
    journal: Journal,
    *,
    slack_writer=None,
    status_writer=None,
    case_id: str | None = None,
) -> list[tuple[EffectRow, ReconciliationResult]]:
    """
    Inspect provider state for every effect whose outcome is still open.

    Bounded by local state, not by provider history. Only effects the journal
    already knows are unfinished are looked at, so this never sweeps an entire
    workspace or repository.
    """
    results: list[tuple[EffectRow, ReconciliationResult]] = []

    for effect in journal.unfinished_effects(case_id):
        import json as _json

        payload = _json.loads(effect.intended_payload_json)

        if effect.provider == "slack" and slack_writer is not None:
            case = journal.get_case(effect.case_id)
            result = reconcile_slack_case(
                slack_writer=slack_writer,
                marker=payload.get("marker", ""),
                known_resource_id=(case.slack_resource_id if case else None)
                or effect.provider_resource_id,
                expected_values=payload.get("expected_values", {}),
            )
        elif effect.provider == "github" and status_writer is not None:
            owner, _, repo = payload.get("repository", "/").partition("/")
            result = reconcile_github_status(
                status_writer=status_writer, owner=owner, repo=repo,
                sha=payload.get("sha", ""), context=payload.get("context", ""),
                intended_state=payload.get("state", ""),
                intended_description=payload.get("description", ""),
            )
        else:
            result = ReconciliationResult(
                ReconciliationOutcome.UNREACHABLE,
                f"no adapter available for provider {effect.provider}",
            )

        if result.outcome is ReconciliationOutcome.ADOPTED:
            journal.transition_effect(
                effect.effect_key, EffectState.VERIFIED,
                provider_resource_id=result.provider_resource_id,
                reconciliation_state=result.outcome.value,
            )
            if effect.provider == "slack" and result.provider_resource_id:
                journal.set_case_slack_resource(
                    effect.case_id, result.provider_resource_id, None
                )
        elif result.outcome is ReconciliationOutcome.ABSENT:
            if effect.state is EffectState.PLANNED:
                # intent was recorded but the provider was never called, and the
                # provider confirms nothing is there. leave it PLANNED so the
                # normal execution path can still carry it out.
                pass
            else:
                journal.transition_effect(
                    effect.effect_key, EffectState.FAILED,
                    last_error=result.detail,
                    reconciliation_state=result.outcome.value,
                )
        elif effect.state is EffectState.PLANNED:
            # a planned effect never reached the provider, so it cannot be
            # uncertain. it stays planned and is attempted normally.
            pass
        else:
            journal.transition_effect(
                effect.effect_key, EffectState.UNKNOWN,
                last_error=result.detail,
                reconciliation_state=result.outcome.value,
            )
        results.append((effect, result))

    return results
