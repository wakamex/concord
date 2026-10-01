"""Seed candidates from an existing decompiler.

The seed only has to be semantically correct and roughly structured; the search
makes it match. concord prefers the most source-shaped correct decompile, which
in practice is Ghidra or Binary Ninja with recovered types, this pointers, named
calls, strings and switch tables, over a machine-shaped lift. A lift stays useful
as the semantics oracle (see oracle.py), not as the seed.
"""

from __future__ import annotations

from concord.model import Candidate, TargetFunction


class Seeder:
    """Produce an initial Candidate for a target function."""

    def __init__(self, backend: str) -> None:
        # backend: "ghidra" or "binaryninja"
        self.backend = backend

    def seed(self, function: TargetFunction) -> Candidate:
        """Return the decompiler's source for one function as the starting
        candidate. Implementations call the chosen backend headless and
        normalize its output into compilable source."""
        raise NotImplementedError
