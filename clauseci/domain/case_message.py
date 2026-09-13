"""
Rendering the Slack engineering case.

The message is written for an engineer who has thirty seconds. It names the
customer, what changed, what the agreement says, where that came from, and what
correction is available.

It deliberately does not contain the eight source documents, any model
reasoning, or any chain of thought. It contains one short quote from the
controlling clause and a pointer to the source.

`expected_fields` returns the meaningful values verification must find when the
message is read back out of Slack. Verification checks those values, not that
some ClauseCI message happens to exist.
"""

from __future__ import annotations

from clauseci.decide import ReleaseDecision
from clauseci.domain.decision import Disposition
from clauseci.domain.execution import ExecutionPlan

QUOTE_LIMIT = 240
SCOPE_NOTE = (
    "Scope: this decision covers the represented log retention configuration and "
    "the recorded source snapshot only. It is not a statement of legal compliance."
)


def _violations(decision: ReleaseDecision):
    return [f for f in decision.findings if f.disposition is Disposition.VIOLATED]


def _review(decision: ReleaseDecision):
    return [f for f in decision.findings if f.disposition is Disposition.REVIEW_REQUIRED]


def _entity_name(decision: ReleaseDecision, customer_id: str) -> str:
    for candidate in decision.candidates:
        for value in candidate.effective_values:
            if value.customer_id == customer_id:
                break
    return customer_id


def render_case(
    decision: ReleaseDecision,
    plan: ExecutionPlan,
    *,
    legal_names: dict[str, str] | None = None,
) -> str:
    """Build the case text. Deterministic for a given decision and plan."""
    legal_names = legal_names or {}
    lines: list[str] = []
    add = lines.append

    short_sha = plan.head_sha[:8]
    add(f"*ClauseCI {decision.actual_head_state.value}* on {plan.repository} "
        f"PR #{plan.pr_number} at `{short_sha}`")
    add("")

    violations = _violations(decision)
    review = _review(decision)

    by_customer: dict[str, list] = {}
    for finding in violations:
        by_customer.setdefault(finding.customer_id, []).append(finding)

    for customer_id, findings in by_customer.items():
        name = legal_names.get(customer_id, customer_id)
        add(f"*Customer:* {name} (`{customer_id}`)")
        add("*Affected configuration:*")
        for finding in sorted(findings, key=lambda f: f.category.value):
            add(f"  - {finding.category.value.replace('_', ' ')}: "
                f"{finding.actual_value} days requested, "
                f"represented limit {finding.represented_limit} days")
        first = findings[0]
        source = first.controlling_source_id or "unknown"
        where = f", section {first.section}" if first.section else ""
        add(f"*Source:* {source}{where}")
        if first.quote:
            quote = " ".join(first.quote.split())
            if len(quote) > QUOTE_LIMIT:
                quote = quote[:QUOTE_LIMIT].rstrip() + "..."
            add(f"*Evidence:* “{quote}”")
        add("")

    for finding in review:
        add(f"*Needs review:* `{finding.customer_id}` "
            f"{finding.category.value.replace('_', ' ')}: {finding.reason}")
    if review:
        add("")

    preferred = decision.preferred_candidate
    if preferred is not None:
        add(f"*Preferred supported correction* (candidate {preferred.candidate_id}):")
        grouped: dict[str, list[str]] = {}
        for value in preferred.effective_values:
            if value.value is None:
                continue
            grouped.setdefault(value.customer_id, []).append(
                f"{value.category.replace('_logs', '')} {value.value}"
            )
        for customer_id in decision.coverage.customers_checked:
            if customer_id in grouped:
                add(f"  - `{customer_id}`: " + ", ".join(sorted(grouped[customer_id])))
        add(f"*Requested behaviour preserved:* "
            f"{preferred.requested_outcomes_preserved} of "
            f"{preferred.requested_outcomes_total} requested category outcomes "
            f"({preferred.customers_fully_preserved} of {preferred.customers_total} "
            f"customers fully preserved)")
        add("_This correction is a proposal. It has not been applied, and it does "
            "not change the status on the current commit._")
        add("")

    unrepresented = decision.coverage.unrepresented_categories
    if unrepresented:
        add(f"*No represented obligation for:* {', '.join(unrepresented)}. "
            f"No limit is inferred for these.")
        add("")

    add(SCOPE_NOTE)
    add("")
    add(f"`{plan.slack_effect.marker}` `sha:{plan.head_sha}` "
        f"`analysis:{plan.analysis_id}`")
    return "\n".join(lines)


def expected_fields(decision: ReleaseDecision, plan: ExecutionPlan) -> dict[str, str]:
    """
    The meaningful values that must appear in the case when it is read back.

    Verification fails if any of these is missing. An unrelated ClauseCI
    message is not accepted just because it exists.
    """
    fields: dict[str, str] = {
        "case_marker": plan.slack_effect.marker,
        "repository": plan.repository,
        "pr_number": f"#{plan.pr_number}",
        "head_sha": plan.head_sha,
        "decision": decision.actual_head_state.value,
    }

    violations = _violations(decision)
    if violations:
        first = violations[0]
        fields["customer"] = first.customer_id
        fields["actual_value"] = str(first.actual_value)
        fields["represented_limit"] = str(first.represented_limit)
        if first.controlling_source_id:
            fields["source"] = first.controlling_source_id

    preferred = decision.preferred_candidate
    if preferred is not None:
        fields["preferred_candidate"] = f"candidate {preferred.candidate_id}"
        fields["preservation"] = (
            f"{preferred.requested_outcomes_preserved} of "
            f"{preferred.requested_outcomes_total} requested category outcomes"
        )
    return fields
