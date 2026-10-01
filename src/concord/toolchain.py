"""The pinned original toolchain and flag inference.

Matching requires the exact compiler version and flags the original build used.
concord runs that compiler in a reproducible container. Flags are inferred once
from a few anchor functions whose source is already known or simple, then held
fixed per translation unit so the codegen search varies only the source.
"""

from __future__ import annotations

from concord.model import CompileResult, TargetFunction


class Toolchain:
    """A pinned compiler in a reproducible container."""

    def __init__(self, image: str, flags: tuple[str, ...] = ()) -> None:
        self.image = image
        self.flags = flags

    def compile(self, source: str, unit: str) -> CompileResult:
        """Compile source for one translation unit and return its object. The
        caller extracts the per-function section via compile.py."""
        raise NotImplementedError

    def infer_flags(self, anchors: list[TargetFunction]) -> tuple[str, ...]:
        """Search flag sets against anchor functions with known source and return
        the set that reproduces their bytes. Sets self.flags on success."""
        raise NotImplementedError
