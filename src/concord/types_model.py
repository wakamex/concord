"""Recover class and struct layout and feed it to the seed and search as fixed input.

Field order, size and padding change codegen directly (member offsets, this
pointer math, vtable shape), so layout has to be right before any codegen search
can converge. concord draws layout from three sources, in rough order of
reliability: a cross-platform debug map (for example a Mac build's symbols and
types for the same program), RTTI and vtables scanned from the target itself, and
data-layout analysis over the lifted code for everything the first two miss.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldLayout:
    name: str
    offset: int
    type_name: str
    size: int


@dataclass
class RecordLayout:
    name: str
    size: int
    fields: list[FieldLayout] = field(default_factory=list)
    vtable_address: int | None = None


class TypeModel:
    """A resolved set of record layouts the rest of the pipeline treats as fixed."""

    def __init__(self) -> None:
        self._records: dict[str, RecordLayout] = {}

    def record(self, name: str) -> RecordLayout | None:
        return self._records.get(name)

    def load_debug_map(self, path: str) -> None:
        """Ingest class and member layout from a cross-platform debug map."""
        raise NotImplementedError

    def load_rtti(self, target: str) -> None:
        """Scan RTTI and vtables from the target to place typeinfos and virtual
        function tables, and name primary vtables and virtual functions."""
        raise NotImplementedError

    def load_dla(self, lifted_ir: str) -> None:
        """Fill remaining record shapes from data-layout analysis over the lift."""
        raise NotImplementedError
