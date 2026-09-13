"""
Deciding which validated clause controls.

Two stages, in this order.

First, deterministic elimination. A source that belongs to another legal entity,
is not executed, is not yet effective, or is outside the customer's corpus
cannot establish a current obligation. No model is consulted for that. The
source stays in the record as a rejected source with its reason.

Second, and only when more than one eligible clause still competes for the same
category, a bounded semantic step reads the clause language and decides. It sees
the candidate clauses only, not whole documents.

Resolution is per category. An amendment that replaces application and
diagnostic retention does not erase an audit clause unless its language says so.
"""

from __future__ import annotations

from typing import Callable

from clauseci.domain.obligations import (
    CandidateRetentionObligation,
    Category,
    Operator,
    RejectedSource,
    ResolutionStatus,
    ResolvedRetentionObligation,
    Unit,
)

#: Called with (customer_id, category, competing candidates) and returns
#: (selected index or None, status, reason, {index: rejection reason}).
ResolutionStep = Callable[
    [str, str, list[CandidateRetentionObligation]],
    tuple[int | None, str, str, dict[int, str]],
]


def resolve_category(
    customer_id: str,
    category: Category,
    candidates: list[CandidateRetentionObligation],
    resolution_step: ResolutionStep | None,
) -> ResolvedRetentionObligation:
    """Resolve one customer and one category from its candidate clauses."""
    covering = [c for c in candidates if category in c.covered_categories]

    eligible = [c for c in covering if c.eligible_to_control]
    ineligible = [c for c in covering if not c.eligible_to_control]

    rejected = [
        RejectedSource(
            source_id=c.source_id,
            reason=(
                f"cannot establish a current obligation: "
                f"{', '.join(c.ineligibility_reasons) or 'not eligible'}"
            ),
        )
        for c in ineligible
    ]

    if not eligible:
        return ResolvedRetentionObligation(
            customer_id=customer_id,
            category=category,
            rejected_sources=tuple(rejected),
            resolution_status=ResolutionStatus.NO_REPRESENTED_OBLIGATION,
            review_reason=(
                "no executed, currently effective source for this customer states a "
                "retention upper bound for this category"
                + (
                    f". {len(ineligible)} ineligible source(s) mention one"
                    if ineligible
                    else ""
                )
            ),
        )

    if len(eligible) == 1:
        return _resolved(customer_id, category, eligible[0], tuple(rejected))

    if resolution_step is None:
        return ResolvedRetentionObligation(
            customer_id=customer_id,
            category=category,
            rejected_sources=tuple(rejected),
            resolution_status=ResolutionStatus.REVIEW_REQUIRED,
            review_reason=(
                f"{len(eligible)} eligible sources state a retention bound for this "
                f"category and no resolution step was available to choose between them"
            ),
        )

    index, status, reason, rejections = resolution_step(customer_id, category.value, eligible)

    for position, candidate in enumerate(eligible):
        if position == index:
            continue
        rejected.append(
            RejectedSource(
                source_id=candidate.source_id,
                reason=rejections.get(position, "not selected as controlling"),
            )
        )

    if status != ResolutionStatus.RESOLVED.value or index is None or not (
        0 <= index < len(eligible)
    ):
        return ResolvedRetentionObligation(
            customer_id=customer_id,
            category=category,
            rejected_sources=tuple(rejected),
            resolution_status=ResolutionStatus.REVIEW_REQUIRED,
            review_reason=reason or "competing clauses could not be resolved",
        )

    return _resolved(customer_id, category, eligible[index], tuple(rejected), reason)


def _resolved(
    customer_id: str,
    category: Category,
    candidate: CandidateRetentionObligation,
    rejected: tuple[RejectedSource, ...],
    reason: str | None = None,
) -> ResolvedRetentionObligation:
    return ResolvedRetentionObligation(
        customer_id=customer_id,
        category=category,
        operator=Operator.AT_MOST,
        value=candidate.value,
        unit=Unit.DAYS,
        covered_categories=candidate.covered_categories,
        controlling_source_id=candidate.source_id,
        controlling_source_file_id=candidate.source_file_id,
        source_digest=candidate.source_content_digest,
        page=candidate.page,
        section=candidate.section,
        quote=candidate.quote,
        rejected_sources=rejected,
        conditions=candidate.conditions,
        exceptions=candidate.exceptions,
        resolution_status=ResolutionStatus.RESOLVED,
        review_reason=reason,
    )
