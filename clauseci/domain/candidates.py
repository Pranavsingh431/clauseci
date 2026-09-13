"""
Bounded correction candidates.

Exactly three constructors, all derived from the actual requested head, the
baseline and the validated obligations. No customer name and no retention
number appears in this module.

Every candidate is recomputed into effective values and put through the same
predicate evaluator as the actual head. A constructor that intended to be safe
proves nothing.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from clauseci.config_resolution import (
    CATEGORY_TO_FIELD,
    RETENTION_FIELDS,
    RETENTION_UNIT,
    RetentionConfig,
    SUPPORTED_CATEGORIES,
    resolve_effective,
)
from clauseci.domain.decision import Disposition, Finding, evaluate_findings
from clauseci.domain.obligations import ResolvedRetentionObligation


class FeasibilityState(str, Enum):
    #: Every represented supported predicate passes for this hypothetical
    #: configuration. Deliberately not called PASS_SCOPED, which belongs only to
    #: a configuration that actually exists.
    FEASIBLE_IN_SCOPE = "FEASIBLE_IN_SCOPE"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


class CandidateType(str, Enum):
    REQUESTED = "REQUESTED"
    BASELINE_RELEVANT_FIELDS = "BASELINE_RELEVANT_FIELDS"
    CUSTOMER_SCOPED_CORRECTION = "CUSTOMER_SCOPED_CORRECTION"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldChange(Frozen):
    config_path: str
    before: int | None
    after: int | None


class CandidateValue(Frozen):
    customer_id: str
    category: str
    value: int | None
    unit: str = RETENTION_UNIT
    source_type: str | None = None
    config_path: str | None = None


class RequestedOutcome(Frozen):
    """One behaviour the pull request asked for, frozen before ranking."""

    customer_id: str
    category: str
    base_value: int | None
    requested_value: int | None


class Candidate(Frozen):
    candidate_id: str
    candidate_type: CandidateType
    description: str

    effective_values: tuple[CandidateValue, ...]
    changes_vs_head: tuple[FieldChange, ...]
    changes_vs_base: tuple[FieldChange, ...]

    findings: tuple[Finding, ...]
    conflicts: tuple[str, ...]
    review_required: tuple[str, ...]

    requested_outcomes_preserved: int
    requested_outcomes_total: int
    customers_fully_preserved: int
    customers_total: int

    feasibility_state: FeasibilityState
    coverage_notes: tuple[str, ...] = ()
    proposed_yaml: str | None = None
    patch: str | None = None

    @property
    def preservation_ratio(self) -> float:
        if not self.requested_outcomes_total:
            return 1.0
        return self.requested_outcomes_preserved / self.requested_outcomes_total


# ------------------------------------------------------------------ helpers

def document_changes(base: RetentionConfig, head: RetentionConfig) -> list[FieldChange]:
    """Raw configuration document differences, not effective value differences."""
    changes: list[FieldChange] = []

    for field in RETENTION_FIELDS:
        before, after = base.defaults.get(field), head.defaults.get(field)
        if before != after:
            changes.append(FieldChange(config_path=f"defaults.{field}",
                                       before=before, after=after))

    for customer_id in sorted(set(base.customers) | set(head.customers)):
        base_block = base.customers.get(customer_id, {})
        head_block = head.customers.get(customer_id, {})
        for field in RETENTION_FIELDS:
            before, after = base_block.get(field), head_block.get(field)
            if before != after:
                changes.append(
                    FieldChange(config_path=f"customers.{customer_id}.{field}",
                                before=before, after=after)
                )
    return changes


def apply_changes(config: RetentionConfig, changes: list[FieldChange]) -> RetentionConfig:
    """Return a new configuration with the given field values written."""
    defaults = dict(config.defaults)
    customers = {cid: dict(block) for cid, block in config.customers.items()}

    for change in changes:
        parts = change.config_path.split(".")
        if parts[0] == "defaults":
            if change.after is None:
                defaults.pop(parts[1], None)
            else:
                defaults[parts[1]] = change.after
        elif parts[0] == "customers":
            customer_id, field = parts[1], parts[2]
            block = customers.setdefault(customer_id, {})
            if change.after is None:
                block.pop(field, None)
            else:
                block[field] = change.after
    return RetentionConfig(defaults=defaults, customers=customers)


def effective_map(
    config: RetentionConfig, cohort: list[str], config_keys: dict[str, str]
) -> dict[tuple[str, str], tuple[int | None, str | None, str | None]]:
    """Resolve every judged category for every cohort customer."""
    resolved: dict[tuple[str, str], tuple[int | None, str | None, str | None]] = {}
    for customer_id in cohort:
        key = config_keys.get(customer_id, customer_id)
        for category in SUPPORTED_CATEGORIES:
            field = CATEGORY_TO_FIELD[category]
            try:
                value = resolve_effective(config, key, field)
            except Exception:  # noqa: BLE001 - absent customer or field
                resolved[(customer_id, category)] = (None, None, None)
            else:
                resolved[(customer_id, category)] = (
                    value.value, value.source.value, value.config_path or None
                )
    return resolved


def requested_outcomes(
    base_values: dict[tuple[str, str], int | None],
    head_values: dict[tuple[str, str], int | None],
) -> list[RequestedOutcome]:
    """
    The behaviour the pull request asked for.

    Frozen from the actual change, before any candidate is built or ranked.
    A category whose effective value did not move was not requested.
    """
    outcomes: list[RequestedOutcome] = []
    for key in sorted(set(base_values) | set(head_values)):
        before, after = base_values.get(key), head_values.get(key)
        if before != after:
            outcomes.append(
                RequestedOutcome(customer_id=key[0], category=key[1],
                                 base_value=before, requested_value=after)
            )
    return outcomes


# ------------------------------------------------------------- constructors

def build_candidates(
    *,
    base_config: RetentionConfig,
    head_config: RetentionConfig,
    cohort: list[str],
    config_keys: dict[str, str],
    obligations: list[ResolvedRetentionObligation],
    head_findings: list[Finding],
) -> list[Candidate]:
    """Build and evaluate exactly three candidates."""
    base_values = {k: v[0] for k, v in effective_map(base_config, cohort, config_keys).items()}
    head_values = {k: v[0] for k, v in effective_map(head_config, cohort, config_keys).items()}
    outcomes = requested_outcomes(base_values, head_values)
    doc_changes = document_changes(base_config, head_config)

    # A: exactly what the pull request asks for.
    candidate_a = ([], "the configuration exactly as the pull request proposes it")

    # B: put every field the pull request moved back to its baseline value.
    revert = [
        FieldChange(config_path=change.config_path, before=change.after, after=change.before)
        for change in doc_changes
    ]
    candidate_b = (revert, "every field the pull request moved, returned to its baseline value")

    # C: keep the request wherever the obligations permit it, and pin only the
    # customer and category pairs that are actually violated back to baseline.
    scoped: list[FieldChange] = []
    for finding in head_findings:
        if finding.disposition is not Disposition.VIOLATED:
            continue
        field = CATEGORY_TO_FIELD[finding.category.value]
        key = config_keys.get(finding.customer_id, finding.customer_id)
        path = f"customers.{key}.{field}"
        current = head_config.customers.get(key, {}).get(field)
        scoped.append(
            FieldChange(config_path=path, before=current,
                        after=base_values.get((finding.customer_id, finding.category.value)))
        )
    candidate_c = (
        scoped,
        "the request kept wherever the represented obligations permit it, with "
        "only the violating customer and category values pinned to baseline",
    )

    built: list[Candidate] = []
    for candidate_id, candidate_type, (changes, description) in (
        ("A", CandidateType.REQUESTED, candidate_a),
        ("B", CandidateType.BASELINE_RELEVANT_FIELDS, candidate_b),
        ("C", CandidateType.CUSTOMER_SCOPED_CORRECTION, candidate_c),
    ):
        config = apply_changes(head_config, changes)
        built.append(
            _evaluate_candidate(
                candidate_id=candidate_id,
                candidate_type=candidate_type,
                description=description,
                config=config,
                head_config=head_config,
                base_config=base_config,
                cohort=cohort,
                config_keys=config_keys,
                obligations=obligations,
                base_values=base_values,
                outcomes=outcomes,
            )
        )
    return built


def _evaluate_candidate(
    *,
    candidate_id: str,
    candidate_type: CandidateType,
    description: str,
    config: RetentionConfig,
    head_config: RetentionConfig,
    base_config: RetentionConfig,
    cohort: list[str],
    config_keys: dict[str, str],
    obligations: list[ResolvedRetentionObligation],
    base_values: dict[tuple[str, str], int | None],
    outcomes: list[RequestedOutcome],
) -> Candidate:
    effective = effective_map(config, cohort, config_keys)
    findings = evaluate_findings(effective, obligations, base_effective=base_values)

    conflicts = tuple(
        f"{f.customer_id} {f.category.value}: {f.actual_value} exceeds {f.represented_limit}"
        for f in findings if f.disposition is Disposition.VIOLATED
    )
    review = tuple(
        f"{f.customer_id} {f.category.value}: {f.reason}"
        for f in findings if f.disposition is Disposition.REVIEW_REQUIRED
    )

    if conflicts:
        state = FeasibilityState.CONFLICT
    elif review:
        state = FeasibilityState.UNKNOWN
    else:
        state = FeasibilityState.FEASIBLE_IN_SCOPE

    values = {k: v[0] for k, v in effective.items()}
    preserved = [
        outcome for outcome in outcomes
        if values.get((outcome.customer_id, outcome.category)) == outcome.requested_value
    ]
    by_customer: dict[str, list[RequestedOutcome]] = {}
    for outcome in outcomes:
        by_customer.setdefault(outcome.customer_id, []).append(outcome)
    fully = sum(
        1 for customer_id, rows in by_customer.items()
        if all(values.get((r.customer_id, r.category)) == r.requested_value for r in rows)
    )

    notes: list[str] = []
    unrepresented = sorted({
        f"{f.customer_id} {f.category.value}" for f in findings
        if f.disposition is Disposition.NO_REPRESENTED_OBLIGATION
    })
    if unrepresented:
        notes.append(
            "no represented cap for: " + ", ".join(unrepresented)
            + ". no limit is inferred for these"
        )

    return Candidate(
        candidate_id=candidate_id,
        candidate_type=candidate_type,
        description=description,
        effective_values=tuple(
            CandidateValue(customer_id=k[0], category=k[1], value=v[0],
                           source_type=v[1], config_path=v[2])
            for k, v in sorted(effective.items())
        ),
        changes_vs_head=tuple(document_changes(head_config, config)),
        changes_vs_base=tuple(document_changes(base_config, config)),
        findings=tuple(findings),
        conflicts=conflicts,
        review_required=review,
        requested_outcomes_preserved=len(preserved),
        requested_outcomes_total=len(outcomes),
        customers_fully_preserved=fully,
        customers_total=len(by_customer),
        feasibility_state=state,
        coverage_notes=tuple(notes),
    )


# ----------------------------------------------------------------- ranking

def rank_candidates(candidates: list[Candidate]) -> tuple[str | None, str]:
    """
    Choose the preferred supported candidate among the evaluated alternatives.

    1. A candidate that conflicts is rejected outright.
    2. A candidate with unresolved coverage never ranks above a known feasible one.
    3. Among feasible candidates, preserve the most requested behaviour.
    4. Tie break on fewer changed fields against the actual requested head.
    5. Tie break on candidate id, so the order never depends on dictionary order.

    This is the preferred candidate among the three evaluated alternatives. It
    is not claimed to be globally optimal or the safest possible configuration.
    """
    usable = [c for c in candidates if c.feasibility_state is not FeasibilityState.CONFLICT]
    if not usable:
        return None, "every candidate conflicts with a represented obligation"

    feasible = [c for c in usable if c.feasibility_state is FeasibilityState.FEASIBLE_IN_SCOPE]
    pool = feasible or usable
    tier = "feasible in scope" if feasible else "unresolved coverage, no feasible candidate exists"

    ordered = sorted(
        pool,
        key=lambda c: (
            -c.requested_outcomes_preserved,
            len(c.changes_vs_head),
            c.candidate_id,
        ),
    )
    winner = ordered[0]
    reason = (
        f"candidate {winner.candidate_id} preserves "
        f"{winner.requested_outcomes_preserved} of {winner.requested_outcomes_total} "
        f"requested category outcomes, the most among candidates that are {tier}, "
        f"changing {len(winner.changes_vs_head)} configuration field(s) against the "
        f"requested head"
    )
    return winner.candidate_id, reason
