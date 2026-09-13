"""
Effect identity and the effect state machine.

An effect is one desired external change. Its key is derived from what the
effect IS, not from when it was created, so the same intended change always
produces the same key and a changed payload produces a different one.

The state machine is small and explicit. Nothing jumps between arbitrary
states, because an illegal jump is how a system starts lying about what it did.
"""

from __future__ import annotations

import hashlib
from enum import Enum

from clauseci.domain.digests import canonical_json


class EffectState(str, Enum):
    #: recorded locally, not yet attempted
    PLANNED = "PLANNED"
    #: about to call the provider, or the call is in progress
    IN_FLIGHT = "IN_FLIGHT"
    #: observed in provider state with matching fields
    VERIFIED = "VERIFIED"
    #: the write may or may not have happened and provider state cannot say
    UNKNOWN = "UNKNOWN"
    #: definitively did not happen
    FAILED = "FAILED"
    #: no longer current, because the head or the corpus moved
    SUPERSEDED = "SUPERSEDED"


TERMINAL_STATES = frozenset({EffectState.VERIFIED, EffectState.FAILED,
                             EffectState.SUPERSEDED})

#: Legal transitions. Anything absent here is rejected.
ALLOWED_TRANSITIONS: dict[EffectState, frozenset[EffectState]] = {
    EffectState.PLANNED: frozenset({
        EffectState.IN_FLIGHT, EffectState.SUPERSEDED, EffectState.FAILED,
    }),
    EffectState.IN_FLIGHT: frozenset({
        EffectState.VERIFIED, EffectState.UNKNOWN, EffectState.FAILED,
        EffectState.SUPERSEDED,
    }),
    EffectState.UNKNOWN: frozenset({
        EffectState.VERIFIED, EffectState.FAILED, EffectState.SUPERSEDED,
    }),
    EffectState.VERIFIED: frozenset(),
    EffectState.FAILED: frozenset(),
    EffectState.SUPERSEDED: frozenset(),
}

#: Leaving IN_FLIGHT for SUPERSEDED means declaring the write never mattered.
#: That is only honest when the external outcome is independently understood,
#: so the caller has to say so explicitly.
NEEDS_INDEPENDENT_EVIDENCE = {(EffectState.IN_FLIGHT, EffectState.SUPERSEDED)}


class IllegalTransition(ValueError):
    """A state change that the machine does not allow."""


def check_transition(
    current: EffectState, target: EffectState, *, outcome_known: bool = False
) -> None:
    """Raise unless this transition is legal."""
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        if current in TERMINAL_STATES:
            raise IllegalTransition(
                f"{current.value} is terminal, it cannot become {target.value}"
            )
        raise IllegalTransition(
            f"{current.value} cannot become {target.value}. allowed: "
            f"{', '.join(sorted(s.value for s in allowed)) or 'nothing'}"
        )
    if (current, target) in NEEDS_INDEPENDENT_EVIDENCE and not outcome_known:
        raise IllegalTransition(
            f"{current.value} may only become {target.value} when the external "
            f"outcome is independently understood"
        )


def is_unfinished(state: EffectState) -> bool:
    """True while the real outcome is still open."""
    return state in {EffectState.PLANNED, EffectState.IN_FLIGHT, EffectState.UNKNOWN}


def build_effect_key(
    *,
    case_id: str,
    analysis_id: str,
    provider: str,
    action_type: str,
    target: str,
    intended_payload_digest: str,
) -> str:
    """
    Deterministic identity for one desired external effect.

    Never random. Two runs that intend the same change derive the same key, and
    a changed payload derives a different one, which is what makes duplicate
    detection meaningful rather than decorative.
    """
    seed = canonical_json({
        "case_id": case_id,
        "analysis_id": analysis_id,
        "provider": provider,
        "action_type": action_type,
        "target": target,
        "intended_payload_digest": intended_payload_digest,
    })
    return "ef-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
