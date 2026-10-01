"""Semantics oracle: reject candidates that drift from the target's behavior.

The search mutates source aggressively to chase bytes. The oracle lifts the
target function once as the semantic reference and runs each candidate against
it on generated inputs before the candidate is allowed to win. Equivalence
between a compiled candidate and a lift is undecidable in general, so this is
differential testing: it finds wrong rewrites but cannot prove their absence. An
exact byte match is its own proof at the machine level; the oracle protects the
intermediate and best-effort candidates. This is the one place a machine-shaped
lift (for example rev.ng) is the right tool: as the reference, not the seed.
"""

from __future__ import annotations

from concord.model import Candidate, EquivalenceResult, TargetFunction


class EquivalenceOracle:
    def __init__(self, method: str = "differential-test") -> None:
        self.method = method

    def reference_ir(self, function: TargetFunction) -> str:
        """Lift the target function once and cache it as the semantic reference."""
        raise NotImplementedError

    def check(self, candidate: Candidate, function: TargetFunction) -> EquivalenceResult:
        """Return whether the candidate agreed with the reference on every tested input."""
        raise NotImplementedError
