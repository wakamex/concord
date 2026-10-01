"""Cause-attributing object diff.

A plain byte or fuzzy score tells you how far a candidate is from the target but
not why, so the search cannot act on it. concord's diff disassembles both sides,
aligns them, and attributes each mismatch to a cause that maps to a fix: a swapped
operand order, a block laid out in the wrong place, a differently allocated
register, an unexpected inline. The search reads the causes to choose its next
transform.
"""

from __future__ import annotations

from concord.model import DiffResult, TargetFunction


class ObjectDiff:
    def diff(self, candidate_code: bytes, function: TargetFunction) -> DiffResult:
        """Compare candidate bytes to the target function and return a scored,
        cause-attributed result. An exact match yields score 100.0, exact True
        and no findings."""
        raise NotImplementedError
