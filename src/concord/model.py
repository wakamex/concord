"""Core data model shared across concord's components.

These are the values that flow through the pipeline: a target function to match,
a candidate source for it, the result of compiling that candidate, and the
attributed diff against the target. Keeping them in one place lets the seed,
compile, diff, oracle, transform and search modules agree on interfaces before
any of them is implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


@dataclass(frozen=True)
class TargetFunction:
    """One function in the target image that concord tries to match."""

    name: str
    address: int
    size: int
    section: str
    unit: str  # translation unit the function is assigned to
    code: bytes = b""  # the target bytes for this function


@dataclass
class Candidate:
    """A source form for one target function, with its provenance."""

    function: str
    source: str
    language: str = "c++"
    # Names of the transforms applied to reach this candidate from the seed,
    # in order. Empty for a seed candidate.
    transforms: tuple[str, ...] = ()


@dataclass
class CompileResult:
    """Result of compiling a candidate with the pinned toolchain."""

    ok: bool
    # Object bytes for the candidate function's section, extracted and placed to
    # match the target's layout. Empty when ok is False.
    code: bytes = b""
    diagnostics: str = ""


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


@dataclass
class EquivalenceResult:
    """The oracle's verdict on a candidate: agreement with the reference on the
    tested inputs, which is evidence of equivalence rather than proof."""

    equivalent: bool
    method: str  # e.g. "differential-test", or "byte-match" for an exact match
    detail: str = ""


@dataclass
class MatchResult:
    """The best candidate concord found for one function."""

    function: str
    best: Candidate
    diff: DiffResult
    equivalence: EquivalenceResult


@dataclass
class Project:
    """A concord project: where the target, toolchain and sources live."""

    root: Path
    target: Path  # the target binary
    toolchain: str  # identifier for the pinned toolchain container
    src_dir: Path
    config_dir: Path
