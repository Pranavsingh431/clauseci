"""
Release sandbox for the public console.

No release rule lives in this file.

ClauseCI's deterministic engine already knows how to turn a configuration and a
set of obligations into findings, a decision state and a ranked set of
correction candidates. This module only rebuilds the inputs that engine takes,
from the sanitized evidence of a verified run, and calls it. If a release rule
changes, the sandbox changes with it, because it does not own a copy.

What it deliberately does not do: no contract is read, no model is called, no
provider is contacted and nothing is written. The obligations come from the
captured run, already verified. The visitor supplies a proposed configuration
and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from clauseci.config_resolution import CATEGORY_TO_FIELD, RetentionConfig
from clauseci.domain.candidates import Candidate, build_candidates, rank_candidates
from clauseci.domain.decision import DecisionState, Finding, decide_state, evaluate_findings
from clauseci.domain.obligations import (
    Category,
    ObligationType,
    Operator,
    ResolutionStatus,
    ResolvedRetentionObligation,
    Unit,
)

#: The demo cohort. The sandbox never invents a customer.
COHORT: tuple[str, ...] = ("acme-corp", "globex", "acme-labs")

#: The two categories the hero change actually requested. Audit is held at its
#: recorded baseline and is not a control, so the sandbox stays the shape of
#: the change the demo is about.
SANDBOX_CATEGORIES: tuple[str, ...] = ("application_logs", "diagnostic_logs")

#: The surface a proposed configuration lives on. The visitor is always
#: proposing a change to it, including when they propose the baseline value,
#: so the decision reads as a release decision rather than as "nothing moved".
SUPPORTED_SURFACE: tuple[str, ...] = ("config/retention.yaml",)

MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 365, 90


@dataclass(frozen=True)
class Row:
    """One customer and category, as the engine judged it."""

    customer_id: str
    category: str
    requested: int | None
    represented_limit: int | None
    disposition: str


@dataclass(frozen=True)
class SandboxResult:
    state: DecisionState
    reason: str
    findings: list[Finding]
    rows: list[Row]
    candidates: list[Candidate]
    preferred: Candidate | None
    ranking_explanation: str

    @property
    def conflicted(self) -> bool:
        return self.state is DecisionState.CONFLICT


def obligations_from_evidence(evidence: dict) -> list[ResolvedRetentionObligation]:
    """
    Rebuild the verified obligations recorded by the captured run.

    These were produced once, by the real analyzer reading the real agreements
    from Drive, and checked against their quotes. The sandbox reuses that
    result rather than re-deriving it.
    """
    obligations: list[ResolvedRetentionObligation] = []
    for finding in evidence["unsafe"]["findings"]:
        absent = finding["disposition"] == "NO_REPRESENTED_OBLIGATION"
        obligations.append(ResolvedRetentionObligation(
            customer_id=finding["customer_id"],
            category=Category(finding["category"]),
            obligation_type=ObligationType.RETENTION_UPPER_BOUND,
            operator=None if absent else Operator.AT_MOST,
            value=None if absent else finding["represented_limit"],
            unit=None if absent else Unit.DAYS,
            controlling_source_id=finding.get("controlling_source_id"),
            section=finding.get("section"),
            quote=finding.get("quote"),
            resolution_status=(ResolutionStatus.NO_REPRESENTED_OBLIGATION if absent
                               else ResolutionStatus.RESOLVED),
        ))
    return obligations


def baseline_from_evidence(evidence: dict) -> dict[tuple[str, str], int | None]:
    """
    The configuration before the pull request, as recorded.

    Candidate B of the captured run is the revert to baseline candidate, so its
    effective values are the baseline. Reading it here keeps the baseline out
    of this file.
    """
    revert = next(c for c in evidence["unsafe"]["candidates"] if c["candidate_id"] == "B")
    return {(v["customer_id"], v["category"]): v["value"] for v in revert["effective_values"]}


def _config(values: dict[tuple[str, str], int | None]) -> RetentionConfig:
    """A configuration document holding exactly these effective values."""
    customers: dict[str, dict[str, int | None]] = {c: {} for c in COHORT}
    for (customer_id, category), value in values.items():
        if customer_id in customers:
            customers[customer_id][CATEGORY_TO_FIELD[category]] = value
    return RetentionConfig(defaults={}, customers=customers)


def evaluate(evidence: dict, requested: dict[str, int]) -> SandboxResult:
    """
    Run ClauseCI's own decision over a proposed configuration.

    `requested` maps a sandbox category to the number of days a visitor is
    proposing for the whole cohort.
    """
    obligations = obligations_from_evidence(evidence)
    baseline = baseline_from_evidence(evidence)

    head = dict(baseline)
    for (customer_id, category) in baseline:
        if category in requested:
            head[(customer_id, category)] = requested[category]

    base_config, head_config = _config(baseline), _config(head)
    config_keys = {customer_id: customer_id for customer_id in COHORT}

    effective = {key: (value, "customer", None) for key, value in head.items()}
    base_effective = dict(baseline)

    findings = evaluate_findings(effective, obligations, base_effective=base_effective)
    state, reason = decide_state(
        findings,
        supported_changed=SUPPORTED_SURFACE,
        unknown_retention_changed=(),
        inventory_known=True,
    )

    candidates = build_candidates(
        base_config=base_config, head_config=head_config, cohort=list(COHORT),
        config_keys=config_keys, obligations=obligations, head_findings=findings,
    )
    preferred_id, explanation = rank_candidates(candidates)
    preferred = next((c for c in candidates if c.candidate_id == preferred_id), None)

    rows = [
        Row(customer_id=f.customer_id, category=f.category.value,
            requested=f.actual_value, represented_limit=f.represented_limit,
            disposition=f.disposition.value)
        for f in findings if f.category.value in SANDBOX_CATEGORIES
    ]

    return SandboxResult(state=state, reason=reason, findings=findings, rows=rows,
                         candidates=candidates, preferred=preferred,
                         ranking_explanation=explanation)
