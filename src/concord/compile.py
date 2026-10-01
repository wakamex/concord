"""Compile a candidate and extract the object bytes for one function.

This is the thin layer between the search and the toolchain: it compiles a
candidate's translation unit, isolates the target function's section, and places
it the way the target is placed (resolving relocations against known symbol
addresses) so the diff compares like with like.
"""

from __future__ import annotations

from concord.model import Candidate, CompileResult, TargetFunction
from concord.toolchain import Toolchain


class CompileHarness:
    def __init__(self, toolchain: Toolchain) -> None:
        self.toolchain = toolchain

    def build(self, candidate: Candidate, function: TargetFunction) -> CompileResult:
        """Compile the candidate and return the placed object bytes for the one
        function, ready to diff against function.code."""
        raise NotImplementedError
