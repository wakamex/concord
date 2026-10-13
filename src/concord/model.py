"""Values shared across concord's modules: the causes a diff attributes, its findings and result, and the oracle's verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Cause(str, Enum):
    """Why a candidate's bytes differ from the target, at a level that maps to a
    fix in source or flags. The search uses the cause to pick its next move."""

    REGISTER_ALLOCATION = "register-allocation"
    BLOCK_ORDER = "block-order"
    OPERAND_ORDER = "operand-order"
    INLINING = "inlining"
    STACK_LAYOUT = "stack-layout"
    STRUCT_LAYOUT = "struct-layout"
    IMMEDIATE_OR_RELOC = "immediate-or-reloc"
    INSTRUCTION_SELECTION = "instruction-selection"
    # Identical bytes, but a relocation resolves to a different destination.
    REFERENCE = "reference"
    # Identical bytes, but the function's place in the target is not proven.
    PLACEMENT = "placement"
    UNKNOWN = "unknown"


@dataclass
class DiffFinding:
    """One attributed mismatch between candidate and target."""

    cause: Cause
    offset: int  # byte offset into the function where the mismatch starts
    detail: str
    candidate: int | None = None  # where the candidate's side of it starts, when it has one


@dataclass
class DiffResult:
    """A cause-attributed comparison of a candidate against the target."""

    score: float  # 0.0 to 100.0, where 100.0 is an exact byte match
    exact: bool
    findings: list[DiffFinding] = field(default_factory=list)
    fuzzy: float | None = None  # objdiff's score of the same compile, as decomp.dev reports it


@dataclass
class EquivalenceResult:
    """The oracle's verdict on a candidate: agreement with the reference on the
    tested inputs, which is evidence of equivalence rather than proof."""

    equivalent: bool
    method: str  # e.g. "differential-test", or "byte-match" for an exact match
    detail: str = ""
