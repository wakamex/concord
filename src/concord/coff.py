"""Read functions from an i386 Common Object File Format (COFF) object, the
relocatable format Microsoft's compilers and Portable Executable delinkers write.

A COFF symbol has no size. A function runs from its symbol to the next function
symbol of the same section, or to the section's end, without the `nop` or `int3`
bytes that pad it to the next alignment boundary: cl pads each function's own
section to 16 bytes, and a delinked object keeps the padding between functions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MACHINE_I386 = 0x14C
FUNCTION_TYPE = 0x20
_HEADER, _SECTION, _SYMBOL, _RELOCATION = 20, 40, 18, 10


@dataclass(frozen=True)
class CoffFunction:
    name: str
    section: str  # the section's name and one-based index, such as .text#4
    start: int  # offset of the function in its section
    code: bytes
    relocations: frozenset[int]  # offsets, from the function's start, of its relocation fields


def is_i386(data: bytes) -> bool:
    return len(data) >= _HEADER and struct.unpack_from("<H", data)[0] == MACHINE_I386


def _name(data: bytes, offset: int) -> str:
    """The symbol or section name stored at `offset`, inline or in the string table."""
    symbol_table, symbol_count = struct.unpack_from("<II", data, 8)
    raw = data[offset : offset + 8]
    if raw[:4] == b"\0\0\0\0":
        start = symbol_table + _SYMBOL * symbol_count + struct.unpack_from("<I", raw, 4)[0]
        return data[start : data.index(b"\0", start)].decode("latin-1")
    return raw.rstrip(b"\0").decode("latin-1")


def lines(data: bytes, symbol: str) -> list[tuple[int, int]]:
    """(offset in the function, source line) for each line record of a function in
    an object compiled with line numbers (cl /Zd), in offset order; empty when the
    object has none for it. A function's records follow one that names its symbol,
    and count lines from the line of its opening brace, which its `.bf` symbol
    gives; the code before the first record is the line after the brace. Code cl
    inlined into the function carries the line of the call."""
    if not is_i386(data):
        raise ValueError("not an i386 COFF object")
    symbol_table, symbol_count = struct.unpack_from("<II", data, 8)
    index = 0
    while index < symbol_count:
        at = symbol_table + _SYMBOL * index
        value, section, kind, _, auxiliary = struct.unpack_from("<IhHBB", data, at + 8)
        if kind == FUNCTION_TYPE and section > 0 and auxiliary and _name(data, at) == symbol:
            begin, _, records = struct.unpack_from("<III", data, at + _SYMBOL)  # its .bf symbol, its size, its line records
            if not records or not 0 < begin < symbol_count:
                return []
            first = struct.unpack_from("<H", data, symbol_table + _SYMBOL * (begin + 1) + 4)[0]
            header = _HEADER + struct.unpack_from("<H", data, 16)[0] + _SECTION * (section - 1)
            table, count = struct.unpack_from("<I", data, header + 28)[0], struct.unpack_from("<H", data, header + 34)[0]
            found = [(0, first + 1)]
            for record in range(records + 6, table + 6 * count, 6):  # after the record naming the symbol
                address, line = struct.unpack_from("<IH", data, record)
                if not line:
                    break  # the next function's records
                found.append((address - value, first + line))
            return sorted(found[1:] if len(found) > 1 and found[1][0] == 0 else found)
        index += 1 + auxiliary
    return []


def functions(data: bytes) -> dict[str, CoffFunction]:
    """Every function the object defines, by symbol name; the first definition of
    a name wins."""
    if not is_i386(data):
        raise ValueError("not an i386 COFF object")
    section_count, _, symbol_table, symbol_count = struct.unpack_from("<HIII", data, 2)
    sections = []
    for index in range(section_count):
        at = _HEADER + struct.unpack_from("<H", data, 16)[0] + _SECTION * index
        size, raw, relocations = struct.unpack_from("<III", data, at + 16)
        count = struct.unpack_from("<H", data, at + 32)[0]
        fields = [struct.unpack_from("<I", data, relocations + _RELOCATION * n)[0] for n in range(count)]
        sections.append((_name(data, at), data[raw : raw + size] if raw else b"", fields))

    defined: dict[int, list[tuple[int, str]]] = {}  # section index -> (value, name) of its function symbols
    index = 0
    while index < symbol_count:
        at = symbol_table + _SYMBOL * index
        value, section, kind, _, auxiliary = struct.unpack_from("<IhHBB", data, at + 8)
        if kind == FUNCTION_TYPE and section > 0:
            defined.setdefault(section, []).append((value, _name(data, at)))
        index += 1 + auxiliary

    found: dict[str, CoffFunction] = {}
    for section, symbols in defined.items():
        name, body, fields = sections[section - 1]
        starts = sorted({value for value, _ in symbols})
        for value, symbol in symbols:
            later = [start for start in starts if start > value]
            code = body[value : later[0] if later else len(body)].rstrip(b"\x90\xcc")
            relocations = frozenset(f - value for f in fields if value <= f < value + len(code))
            found.setdefault(symbol, CoffFunction(symbol, f"{name}#{section}", value, code, relocations))
    return found
