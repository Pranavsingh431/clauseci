"""
Deterministic fault injection. TEST INFRASTRUCTURE ONLY.

Production passes NO_FAULTS, which is a null object whose checks do nothing.
The workflow calls the same small set of named boundaries either way, so there
are no scenario identifiers or conditional branches scattered through the
execution path.

A fault raised BEFORE a provider call means nothing was written. A fault raised
AFTER the response means the write really happened and the client lost the
answer. Those are different situations and the recovery for each is different,
which is exactly why they are separate boundaries.
"""

from __future__ import annotations

from enum import Enum


class FaultPoint(str, Enum):
    #: nothing has been sent to the provider yet
    BEFORE_PROVIDER_CALL = "BEFORE_PROVIDER_CALL"
    #: the provider accepted the write and the client is about to record it
    AFTER_PROVIDER_RESPONSE = "AFTER_PROVIDER_RESPONSE"
    #: provider state has been read and is about to be compared
    ON_READBACK = "ON_READBACK"
    #: the three freshness boundaries
    FRESHNESS_BEFORE_EFFECTS = "FRESHNESS_BEFORE_EFFECTS"
    FRESHNESS_BETWEEN_EFFECTS = "FRESHNESS_BETWEEN_EFFECTS"
    FRESHNESS_BEFORE_SEAL = "FRESHNESS_BEFORE_SEAL"


class InjectedFailure(RuntimeError):
    """Simulated failure before the provider was contacted. Nothing was written."""


class InjectedResponseLoss(RuntimeError):
    """
    Simulated loss of the client response after a real provider write.

    The provider accepted the change. Only the local process lost the answer.
    """


class NullFaults:
    """Production behaviour. Every check is a no operation."""

    def check(self, point: FaultPoint, *, provider: str = "", action: str = "") -> None:
        return None

    def mutate_readback(self, provider: str, observed):
        return observed

    def override_freshness(self, point: FaultPoint, actual):
        return actual


NO_FAULTS = NullFaults()


class FaultPlan(NullFaults):
    """A scripted set of faults for one test."""

    def __init__(self) -> None:
        self.raise_before: set[tuple[str, str]] = set()
        self.lose_response: set[tuple[str, str]] = set()
        self.readback_mutators: dict[str, object] = {}
        self.freshness_overrides: dict[FaultPoint, object] = {}
        self.fired: list[str] = []

    # --------------------------------------------------------- scripting

    def fail_before_call(self, provider: str, action: str = "*") -> "FaultPlan":
        self.raise_before.add((provider, action))
        return self

    def write_succeeds_response_lost(self, provider: str, action: str = "*") -> "FaultPlan":
        self.lose_response.add((provider, action))
        return self

    def wrong_readback(self, provider: str, mutator) -> "FaultPlan":
        self.readback_mutators[provider] = mutator
        return self

    def freshness_changes_at(self, point: FaultPoint, state) -> "FaultPlan":
        self.freshness_overrides[point] = state
        return self

    # ------------------------------------------------------------ hooks

    def _matches(self, entries: set[tuple[str, str]], provider: str, action: str) -> bool:
        return (provider, action) in entries or (provider, "*") in entries

    def check(self, point: FaultPoint, *, provider: str = "", action: str = "") -> None:
        if point is FaultPoint.BEFORE_PROVIDER_CALL and self._matches(
            self.raise_before, provider, action
        ):
            self.fired.append(f"{point.value}:{provider}:{action}")
            raise InjectedFailure(
                f"injected failure before the {provider} call. nothing was written"
            )
        if point is FaultPoint.AFTER_PROVIDER_RESPONSE and self._matches(
            self.lose_response, provider, action
        ):
            self.fired.append(f"{point.value}:{provider}:{action}")
            raise InjectedResponseLoss(
                f"injected client response loss after a real {provider} write. "
                f"the provider accepted it, this process lost the answer"
            )

    def mutate_readback(self, provider: str, observed):
        mutator = self.readback_mutators.get(provider)
        if mutator is None:
            return observed
        self.fired.append(f"ON_READBACK:{provider}")
        return mutator(observed)

    def override_freshness(self, point: FaultPoint, actual):
        override = self.freshness_overrides.get(point)
        if override is None:
            return actual
        self.fired.append(f"{point.value}")
        return override
