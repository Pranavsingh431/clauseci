"""
The deterministic release decision.

Nothing here calls a model. It takes effective configuration values from the
snapshot and validated obligations from the analyzer, and applies the supported
predicate.

The predicate is small on purpose: for one customer and one category, is the
effective retention value at most the represented cap. Everything else is about
knowing when that question cannot be answered honestly.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from clauseci.config_resolution import CATEGORY_TO_FIELD, RETENTION_UNIT
from clauseci.domain.obligations import (
    Category,
    ResolutionStatus,
    ResolvedRetentionObligation,
)


class Disposition(str, Enum):
    #: The effective value is within the represented cap.
    SATISFIED = "SATISFIED"
    #: The effective value exceeds the represented cap.
    VIOLATED = "VIOLATED"
    #: The reviewed sources establish no cap for this category. This is not
    #: permission, and it is not the same as SATISFIED.
    NO_REPRESENTED_OBLIGATION = "NO_REPRESENTED_OBLIGATION"
    #: The question could not be answered safely.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class DecisionState(str, Enum):
    #: Every represented supported predicate passed, for this exact recorded
    #: configuration and this exact source snapshot. Not general compliance.
    PASS_SCOPED = "PASS_SCOPED"
    #: At least one represented supported predicate is violated.
    CONFLICT = "CONFLICT"
    #: Something needed for the decision is missing, ambiguous or unsupported.
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    #: The changed file inventory is known and no supported retention surface
    #: changed, and nothing retention like is unrecognised.
    NO_SUPPORTED_CHANGE = "NO_SUPPORTED_CHANGE"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Finding(Frozen):
    """One evaluated customer and category."""

    customer_id: str
    category: Category

    actual_value: int | None
    actual_unit: str = RETENTION_UNIT
    value_source_type: str | None = None
    value_config_path: str | None = None

    obligation_status: ResolutionStatus
    represented_limit: int | None = None
    operator: str | None = None
    controlling_source_id: str | None = None
    controlling_source_file_id: str | None = None
    source_digest: str | None = None
    page: int | None = None
    section: str | None = None
    quote: str | None = None

    disposition: Disposition
    reason: str

    # change awareness
    base_value: int | None = None
    head_value: int | None = None
    changed_by_pr: bool = False
    #: violated at the base revision
    present_in_base: bool = False
    #: violated at the head revision
    present_in_head: bool = False
    #: violated at head but not at base
    introduced_by_pr: bool = False
    #: violated at base but not at head
    resolved_by_pr: bool = False


class Coverage(Frozen):
    """What was actually checked, so the claim can be read honestly."""

    supported_surfaces_checked: tuple[str, ...] = ()
    customers_checked: tuple[str, ...] = ()
    categories_checked: tuple[str, ...] = ()
    #: categories with no represented cap in the reviewed sources
    unrepresented_categories: tuple[str, ...] = ()
    unsupported_changed_files: tuple[str, ...] = ()
    unknown_retention_related_files: tuple[str, ...] = ()
    source_snapshot_digest: str = ""
    head_sha: str = ""


class EvidenceBinding(Frozen):
    """Everything the decision is pinned to."""

    repository: str
    pr_number: int
    base_sha: str
    head_sha: str
    corpus_digest: str
    snapshot_schema_version: str
    parser_version: str
    policy_version: str
    decision_policy_version: str
    decision_schema_version: str
    semantic_model: str
    semantic_prompt_version: str
    semantic_schema_version: str


def evaluate_findings(
    effective: dict[tuple[str, str], tuple[int | None, str | None, str | None]],
    obligations: list[ResolvedRetentionObligation],
    *,
    base_effective: dict[tuple[str, str], int | None] | None = None,
) -> list[Finding]:
    """
    Apply the supported predicate to every customer and category.

    `effective` maps (customer_id, category) to (value, source_type, config_path).
    `obligations` are the validated resolved obligations from the analyzer.

    This same function evaluates the actual head and every hypothetical
    candidate. A candidate is never trusted because its constructor meant well.
    """
    by_key = {(o.customer_id, o.category.value): o for o in obligations}
    findings: list[Finding] = []

    for (customer_id, category), (value, source_type, config_path) in sorted(effective.items()):
        obligation = by_key.get((customer_id, category))
        base_value = (base_effective or {}).get((customer_id, category))

        if obligation is None:
            findings.append(
                _finding(
                    customer_id, category, value, source_type, config_path, base_value,
                    obligation_status=ResolutionStatus.REVIEW_REQUIRED,
                    disposition=Disposition.REVIEW_REQUIRED,
                    reason="no obligation record exists for this customer and category",
                )
            )
            continue

        if obligation.resolution_status is ResolutionStatus.REVIEW_REQUIRED:
            findings.append(
                _finding(
                    customer_id, category, value, source_type, config_path, base_value,
                    obligation_status=obligation.resolution_status,
                    disposition=Disposition.REVIEW_REQUIRED,
                    reason=obligation.review_reason or "the controlling source is unresolved",
                    obligation=obligation,
                )
            )
            continue

        if obligation.resolution_status is ResolutionStatus.NO_REPRESENTED_OBLIGATION:
            findings.append(
                _finding(
                    customer_id, category, value, source_type, config_path, base_value,
                    obligation_status=obligation.resolution_status,
                    disposition=Disposition.NO_REPRESENTED_OBLIGATION,
                    reason=(
                        "the reviewed sources establish no retention cap for this "
                        "category. that is not permission and no limit is inferred"
                    ),
                    obligation=obligation,
                )
            )
            continue

        if value is None:
            findings.append(
                _finding(
                    customer_id, category, value, source_type, config_path, base_value,
                    obligation_status=obligation.resolution_status,
                    disposition=Disposition.REVIEW_REQUIRED,
                    reason="the configuration produces no effective value for this category",
                    obligation=obligation,
                )
            )
            continue

        limit = obligation.value
        satisfied = value <= limit
        base_violates = base_value is not None and base_value > limit

        findings.append(
            _finding(
                customer_id, category, value, source_type, config_path, base_value,
                obligation_status=obligation.resolution_status,
                disposition=Disposition.SATISFIED if satisfied else Disposition.VIOLATED,
                reason=(
                    f"{value} {RETENTION_UNIT} is within the represented limit of "
                    f"{limit}" if satisfied else
                    f"{value} {RETENTION_UNIT} exceeds the represented limit of {limit}"
                ),
                obligation=obligation,
                present_in_base=base_violates,
                present_in_head=not satisfied,
            )
        )

    return findings


def _finding(
    customer_id: str,
    category: str,
    value: int | None,
    source_type: str | None,
    config_path: str | None,
    base_value: int | None,
    *,
    obligation_status: ResolutionStatus,
    disposition: Disposition,
    reason: str,
    obligation: ResolvedRetentionObligation | None = None,
    present_in_base: bool = False,
    present_in_head: bool = False,
) -> Finding:
    return Finding(
        customer_id=customer_id,
        category=Category(category),
        actual_value=value,
        value_source_type=source_type,
        value_config_path=config_path,
        obligation_status=obligation_status,
        represented_limit=obligation.value if obligation else None,
        operator=obligation.operator.value if obligation and obligation.operator else None,
        controlling_source_id=obligation.controlling_source_id if obligation else None,
        controlling_source_file_id=obligation.controlling_source_file_id if obligation else None,
        source_digest=obligation.source_digest if obligation else None,
        page=obligation.page if obligation else None,
        section=obligation.section if obligation else None,
        quote=obligation.quote if obligation else None,
        disposition=disposition,
        reason=reason,
        base_value=base_value,
        head_value=value,
        changed_by_pr=base_value is not None and base_value != value,
        present_in_base=present_in_base,
        present_in_head=present_in_head,
        introduced_by_pr=present_in_head and not present_in_base,
        resolved_by_pr=present_in_base and not present_in_head,
    )


def decide_state(
    findings: list[Finding],
    *,
    supported_changed: tuple[str, ...],
    unknown_retention_changed: tuple[str, ...],
    inventory_known: bool,
    extra_review_reasons: tuple[str, ...] = (),
) -> tuple[DecisionState, str]:
    """
    Turn findings into one state.

    Precedence, highest first:

    1. CONFLICT. A confirmed violation is the strongest statement available and
       is never softened into review required, even when something else is also
       unclear. The unclear thing is still recorded in coverage and warnings.
    2. REVIEW_REQUIRED. Something needed is missing, ambiguous or unrecognised.
       An unrecognised retention like file lands here and can never be green.
    3. NO_SUPPORTED_CHANGE. Only when the inventory is known, nothing supported
       changed, and nothing retention like is unrecognised.
    4. PASS_SCOPED.
    """
    violations = [f for f in findings if f.disposition is Disposition.VIOLATED]
    if violations:
        introduced = [f for f in violations if f.introduced_by_pr]
        pre_existing = [f for f in violations if not f.introduced_by_pr]
        parts = []
        if introduced:
            parts.append(f"{len(introduced)} introduced by this pull request")
        if pre_existing:
            parts.append(f"{len(pre_existing)} already present at the base revision")
        return DecisionState.CONFLICT, (
            f"{len(violations)} represented retention predicate(s) violated: "
            + ", ".join(parts)
        )

    reasons = list(extra_review_reasons)
    if unknown_retention_changed:
        reasons.append(
            "changed file(s) look retention related but their schema is not "
            f"supported: {', '.join(unknown_retention_changed)}"
        )
    review = [f for f in findings if f.disposition is Disposition.REVIEW_REQUIRED]
    if review:
        reasons.append(
            f"{len(review)} customer/category pair(s) could not be evaluated safely"
        )
    if reasons:
        return DecisionState.REVIEW_REQUIRED, "; ".join(reasons)

    if not inventory_known:
        return DecisionState.REVIEW_REQUIRED, "the changed file inventory is not known"

    if not supported_changed:
        return DecisionState.NO_SUPPORTED_CHANGE, (
            "no supported retention surface changed and nothing retention like "
            "is unrecognised"
        )

    return DecisionState.PASS_SCOPED, (
        "every represented supported retention predicate passed for this exact "
        "configuration and source snapshot"
    )
