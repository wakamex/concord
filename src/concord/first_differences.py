"""Group a gruntz-decomp checkout's open functions by what their differing code calls.

cl 5.0 gives code it inlined the line of the call, so the source lines behind a function's differing
instructions name the helpers, macros and functions whose code differs, as well as the function's own
statements. A helper that recurs across many open functions is a candidate for one shared fix in its
header, where a body rewrite of each caller would not reach it.
"""

from __future__ import annotations

import re
from collections import defaultdict

from concord import coff

# Words followed by a parenthesis that are not calls.
NOT_CALLS = frozenset(
    ["if", "for", "while", "switch", "return", "sizeof", "catch", "case", "new", "delete", "defined", "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast", "RVA"]
)
CALL = re.compile(rb"\b([A-Za-z_]\w*)\s*\(")


def callees(line: bytes) -> list[str]:
    """The names called on one source line, in order, without repeats: functions, member
    functions and function-like macros."""
    found = []
    for match in CALL.finditer(line.split(b"//")[0]):
        name = match[1].decode()
        if name not in NOT_CALLS and name not in found:
            found.append(name)
    return found


def at(records: list[tuple[int, int]], offset: int) -> int | None:
    """The source line of the code at `offset`, from a function's (offset, line) records."""
    before = [line for start, line in records if start <= offset]
    return before[-1] if before else None


def describe(target, verdict, data: bytes, lines: list[bytes]) -> dict:
    """One open function: the cause of its first difference and the lines and callees behind
    every differing instruction, with the line of the first."""
    diff = target.diff(verdict)
    records = coff.lines(data, verdict.symbol)
    placed = sorted((finding for finding in diff.findings if finding.candidate is not None), key=lambda f: f.candidate)
    first = min(diff.findings, key=lambda f: f.offset) if diff.findings else None
    differing = sorted({line for finding in placed if (line := at(records, finding.candidate)) is not None})
    first_line = at(records, placed[0].candidate) if placed else None

    def called(numbers):
        return list(dict.fromkeys(name for n in numbers if 0 < n <= len(lines) for name in callees(lines[n - 1])))

    return {
        "unit": verdict.unit,
        "symbol": verdict.symbol,
        "address": verdict.row.get("address"),
        "size": verdict.row.get("size", 0),
        "best": verdict.row.get("best"),
        "cause": first.cause.value if first else None,
        "first_line": first_line,
        "first_text": lines[first_line - 1].decode(errors="replace").strip() if first_line and first_line <= len(lines) else None,
        "first_callees": called([first_line] if first_line else []),
        "lines": differing,
        "callees": called(differing),
    }


def rank(functions: list[dict], key: str) -> list[dict]:
    """Each callee named by `key` ("callees" or "first_callees"), with the open functions and
    bytes behind it, most functions first."""
    found = defaultdict(list)
    for function in functions:
        for name in function[key]:
            found[name].append(function)
    rows = [
        {"callee": name, "functions": len(group), "bytes": sum(f["size"] for f in group), "symbols": [f["symbol"] for f in group]}
        for name, group in found.items()
    ]
    return sorted(rows, key=lambda row: (-row["functions"], -row["bytes"], row["callee"]))


def run(target, progress=None) -> dict:
    """Describe every open function of the checkout's last build, one line-record compile per unit."""
    open_ = defaultdict(list)
    for verdict in target.verdicts():
        if not verdict.row["exact"]:
            open_[verdict.unit].append(verdict)
    functions, failed = [], []
    for unit, verdicts in sorted(open_.items()):
        source = target.source_path(unit).read_bytes()
        data = target.line_object(unit, source)
        if data is None:
            failed.append(unit)
            continue
        lines = source.splitlines()
        for verdict in verdicts:
            try:
                functions.append(describe(target, verdict, data, lines))
            except (KeyError, ValueError) as error:
                failed.append(f"{unit} {verdict.symbol}: {error}")
        if progress:
            progress(unit, len(functions))
    causes = defaultdict(lambda: [0, 0])
    for function in functions:
        causes[function["cause"]][0] += 1
        causes[function["cause"]][1] += function["size"]
    return {
        "functions": functions,
        "failed": failed,
        "causes": {cause: {"functions": n, "bytes": size} for cause, (n, size) in sorted(causes.items(), key=lambda item: -item[1][0])},
        "by_callee": rank(functions, "callees"),
        "by_first_callee": rank(functions, "first_callees"),
    }
