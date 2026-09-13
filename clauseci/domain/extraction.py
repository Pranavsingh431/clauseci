"""
Turning a model reading into a validated candidate obligation.

The model supplies language. This module supplies identity, and refuses
anything it cannot check.

A verified quote proves the text came from that document. It does not prove the
reading is legally correct. Those are different claims and only the first one is
mechanically checkable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from clauseci.domain.models import DriveSourceSnapshot
from clauseci.domain.obligations import (
    ALLOWED_CATEGORIES,
    CandidateRetentionObligation,
    Category,
    ModelCandidate,
    Operator,
    SemanticStatus,
    Unit,
)
from clauseci.registry import CustomerRegistry
from clauseci.sources import SourceEligibility, TrustedSource

# Minimum quote length. A two word quote can match by accident.
MIN_QUOTE_CHARS = 20

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
          "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]

_QUOTE_CHARS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


@dataclass(frozen=True)
class ValidationFailure:
    """A candidate that was refused, and why."""

    source_id: str
    reason: str


def normalise_for_match(text: str) -> str:
    """
    Make text comparable across PDF extraction quirks.

    Collapses whitespace and normalises typographic punctuation. Case is kept,
    because a quote that differs in case is not a verbatim quote.
    """
    normalised = unicodedata.normalize("NFKC", text)
    for original, replacement in _QUOTE_CHARS.items():
        normalised = normalised.replace(original, replacement)
    return re.sub(r"\s+", " ", normalised).strip()


def spell_number(value: int) -> str:
    """English cardinal for a whole number, in the style contracts use."""
    if value < 20:
        return _UNITS[value]
    if value < 100:
        tens, remainder = divmod(value, 10)
        return _TENS[tens] + (f"-{_UNITS[remainder]}" if remainder else "")
    if value < 1000:
        hundreds, remainder = divmod(value, 100)
        text = f"{_UNITS[hundreds]} hundred"
        return f"{text} and {spell_number(remainder)}" if remainder else text
    thousands, remainder = divmod(value, 1000)
    text = f"{spell_number(thousands)} thousand"
    return f"{text} {spell_number(remainder)}" if remainder else text


def quote_states_value(quote: str, value: int) -> bool:
    """
    True when the quote actually states the number being claimed.

    A retention bound of N days must be supported by text that says N, either
    as a numeral or spelled out. This is what stops a real but irrelevant
    sentence from being used to justify an invented number. Contract drafting
    usually writes both forms, as in "thirty (30) days".
    """
    normalised = normalise_for_match(quote)
    if re.search(rf"(?<![0-9]){value}(?![0-9])", normalised):
        return True
    return spell_number(value).lower() in normalised.lower()


def quote_occurs_in(quote: str, document_text: str) -> bool:
    """True when the quote appears verbatim in the document text."""
    if not quote or len(quote.strip()) < MIN_QUOTE_CHARS:
        return False
    return normalise_for_match(quote) in normalise_for_match(document_text)


def validate_candidate(
    candidate: ModelCandidate,
    *,
    customer_id: str,
    source: TrustedSource,
    snapshot_source: DriveSourceSnapshot,
    eligibility: SourceEligibility,
    document_text: str,
    registry: CustomerRegistry,
    expected_file_id: str,
    expected_digest: str,
) -> CandidateRetentionObligation | ValidationFailure:
    """
    Check one model candidate against everything that can be checked.

    Identity is stamped from the snapshot and the manifest, never taken from the
    model. If any check fails the candidate is refused rather than repaired.
    """
    reject = lambda reason: ValidationFailure(source_id=source.source_id, reason=reason)  # noqa: E731

    # identity of the customer
    try:
        registry.by_id(customer_id)
    except Exception:  # noqa: BLE001
        return reject(f"customer {customer_id!r} is not in the registry")

    # the source really belongs to this customer
    if source.customer_id != customer_id:
        return reject(
            f"source {source.source_id} belongs to {source.customer_id}, "
            f"not to {customer_id}"
        )
    registered = registry.by_id(customer_id)
    if source.document_name not in registered.eligible_documents:
        return reject(
            f"{source.document_name} is not in the corpus for {customer_id}"
        )

    # the snapshot this candidate is bound to
    if snapshot_source.file_id != expected_file_id:
        return reject(
            f"file id {snapshot_source.file_id} does not match the snapshot "
            f"({expected_file_id})"
        )
    if snapshot_source.content_sha256 != expected_digest:
        return reject("content digest does not match the snapshot")

    # supported predicate
    if candidate.operator != Operator.AT_MOST.value:
        return reject(f"operator {candidate.operator!r} is not supported")
    if candidate.unit != Unit.DAYS.value:
        return reject(f"unit {candidate.unit!r} is not supported")
    if not isinstance(candidate.value, int) or candidate.value <= 0:
        return reject(f"value {candidate.value!r} is not a positive whole number of days")

    if not candidate.covered_categories:
        return reject("no category was named")
    unknown = [c for c in candidate.covered_categories if c not in ALLOWED_CATEGORIES]
    if unknown:
        return reject(f"unsupported category or categories: {', '.join(unknown)}")

    # evidence binding
    if not quote_occurs_in(candidate.quote, document_text):
        return reject("the quote does not occur verbatim in the source text")
    if not quote_states_value(candidate.quote, candidate.value):
        return reject(
            f"the quote does not state {candidate.value}, so it cannot support a "
            f"bound of {candidate.value} days"
        )

    return CandidateRetentionObligation(
        customer_id=customer_id,
        source_id=source.source_id,
        source_file_id=snapshot_source.file_id,
        source_content_digest=snapshot_source.content_sha256,
        value=candidate.value,
        covered_categories=tuple(Category(c) for c in candidate.covered_categories),
        page=candidate.page,
        section=candidate.section,
        quote=candidate.quote,
        document_declared_execution_status=candidate.document_declared_execution_status,
        document_declared_effective_date=candidate.document_declared_effective_date,
        amendment_reference=candidate.amendment_reference,
        supersession_reference=candidate.supersession_reference,
        conditions=tuple(candidate.conditions),
        exceptions=tuple(candidate.exceptions),
        semantic_status=SemanticStatus.EXTRACTED,
        quote_verified=True,
        eligible_to_control=eligibility.eligible_to_control,
        ineligibility_reasons=tuple(r.value for r in eligibility.reasons),
    )
