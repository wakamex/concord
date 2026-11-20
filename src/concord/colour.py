"""Read MSVC 5.0's global register colouring of one gruntz-decomp function from c2.exe's memory.

When c2.exe's code generator starts a function, global colouring has finished and its live ranges are still in
a hash table of 1,024 buckets keyed by `id & 0x3ff`. Each range records the symbol it belongs to, its priority
(the colouring queue runs in descending priority, and each range takes the first free register in the order
eax, ecx, edx), the register it got and its reference count. concord runs c2.exe on the unit's captured input
under winedbg, stops there, and reads the table and the records it points to, over as many runs as it takes
to follow the pointers: heap addresses repeat between runs of the same input.
"""

from __future__ import annotations

import re

from concord import coff
from concord.regtrace import DESCRIPTOR_SIZE, DESCRIPTORS, REGISTERS

TABLE, BUCKETS = 0x493238, 1024
RECORD_WORDS, SYMBOL_WORDS, NAME_WORDS = 17, 12, 4
DUMP = re.compile(r"^0x([0-9a-f]+)(?: \S+)?:\s+((?:[0-9a-f]{8}\s*)+)$", re.MULTILINE)


def memory(log: str) -> dict[int, int]:
    """The 32-bit words winedbg's `x` commands printed, by address."""
    words = {}
    for address, row in DUMP.findall(log):
        for k, word in enumerate(row.split()):
            words[int(address, 16) + 4 * k] = int(word, 16)
    return words


def signed(word: int) -> int:
    return word - (1 << 32) if word & 0x80000000 else word


def register(word: int) -> str | None:
    number, rest = divmod(word - DESCRIPTORS, DESCRIPTOR_SIZE)
    return REGISTERS.get(number) if word and rest == 0 else None


def ranges(words: dict[int, int], heads: list[int]) -> list[dict]:
    """The live ranges reachable from the bucket heads, in colouring order (descending priority)."""
    found, todo = {}, list(heads)
    while todo:
        at = todo.pop()
        if at in found or at + 4 * (RECORD_WORDS - 1) not in words:
            continue
        record = [words[at + 4 * i] for i in range(RECORD_WORDS)]
        symbol = record[0]
        name = words.get(symbol)
        found[at] = {
            "id": record[7],
            "priority": signed(record[3]),
            "register": register(record[4]),
            "references": record[9],
            "symbol_kind": "named" if words.get(symbol + 4, 0) & 0x800 else "temporary",
            "symbol_number": words.get(symbol + 0x1C),
            "frame_offset": signed(words[name + 0xC]) if name and name + 0xC in words else None,
        }
        if record[11]:
            todo.append(record[11])
    return sorted(found.values(), key=lambda row: (-row["priority"], row["id"]))


def wanted(words: dict[int, int], heads: list[int]) -> list[tuple[int, int]]:
    """The memory still to read: records, their chained neighbours, their symbols and the symbols' names."""
    need, seen, todo = [], set(), list(heads)
    while todo:
        at = todo.pop()
        if at in seen:
            continue
        seen.add(at)
        if at + 4 * (RECORD_WORDS - 1) not in words:
            need.append((at, RECORD_WORDS))
            continue
        chained, symbol = words[at + 0x2C], words[at]
        if chained:
            todo.append(chained)
        if symbol and symbol + 4 * (SYMBOL_WORDS - 1) not in words:
            need.append((symbol, SYMBOL_WORDS))
        elif symbol and words.get(symbol) and words[symbol] + 0xC not in words:
            need.append((words[symbol], NAME_WORDS))
    return need


def run(target, unit: str, symbol: str, source: bytes, limit: int = 6) -> dict:
    """The function's live ranges after global colouring, read from c2.exe's memory."""
    il, evaluation = target.capture_il(unit, source)
    if il is None or evaluation.object is None:
        raise RuntimeError(f"the unit did not compile: {evaluation.error[-2000:]}")
    raw = evaluation.object.with_name("capture.raw.obj")
    order = list(coff.functions((raw if raw.exists() else evaluation.object).read_bytes()))
    if symbol not in order:
        raise KeyError(f"{symbol} is not a function of {unit}'s object")
    stop = order.index(symbol) + 1
    words = memory(target.peek(unit, il, stop, [(TABLE, BUCKETS)]))
    heads = [words[TABLE + 4 * i] for i in range(BUCKETS) if words.get(TABLE + 4 * i)]
    for _ in range(limit):
        need = wanted(words, heads)
        if not need:
            break
        words |= memory(target.peek(unit, il, stop, need))
    verdict = evaluation.verdict(symbol)
    return {"symbol": symbol, "score": verdict.row["fuzzy"] if verdict else None, "ranges": ranges(words, heads)}
