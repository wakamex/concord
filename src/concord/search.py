"""The search loop: converge a candidate to a byte match.

The loop is: compile the current candidate, diff it against the target, read the
attributed causes, apply the transforms that address those causes to produce new
candidates, keep the ones the oracle accepts, and move to the best scoring one.
Repeat until the diff is exact or the search budget runs out. Cause attribution
makes this directed rather than random, and the oracle filters out rewrites that
changed behavior on its tested inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from concord.compile import CompileHarness
from concord.diff import ObjectDiff
from concord.model import Candidate, MatchResult, TargetFunction
from concord.oracle import EquivalenceOracle
from concord.transforms import registry


@dataclass
class SearchBudget:
    max_iterations: int = 1000
    max_seconds: float = 600.0


class Searcher:
    def __init__(
        self,
        harness: CompileHarness,
        differ: ObjectDiff,
        oracle: EquivalenceOracle,
        budget: SearchBudget | None = None,
    ) -> None:
        self.harness = harness
        self.differ = differ
        self.oracle = oracle
        self.budget = budget or SearchBudget()
        self.transforms = registry()

    def match(self, seed: Candidate, function: TargetFunction) -> MatchResult:
        """Search from the seed for a byte match to function, returning the best
        candidate found with its diff and equivalence evidence."""
        raise NotImplementedError
