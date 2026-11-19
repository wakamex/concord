"""Give a pointer a second use, so that MSVC 5.0 keeps the round-robin cursor where the original had it.

c2.exe hands a temporary used once to its local allocator, which takes the next of eax, ecx and edx and
advances a cursor. A pointer used twice goes to the global colouring pass instead and spends no step. When
every register up to a pointer load agrees with retail and the next one is a step ahead, the original held
that pointer globally. Passing the pointer through an inline helper that reports a null pointer with MFC's
TRACE supplies the second use: a release build compiles TRACE to nothing, so the check leaves no code, as
debug logging in the original's release build would have.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from tree_sitter import Node

from concord import regtrace

HELPER_NAME = b"CheckedPointer"
HELPER = b"template <class T>\ninline T* CheckedPointer(T* pointer) {\n    if (pointer == NULL)\n        TRACE(\"null pointer\\n\");\n    return pointer;\n}\n"
LOAD = re.compile(r"mov e\w\w, dword ptr \[(?!esp\b)e\w\w(?: \+ 0x[0-9a-f]+)?\]")
INCLUDE = re.compile(rb"(?m)^#include\b.*\r?\n")
MACRO = re.compile(rb"[A-Z][A-Z0-9_]+")
POINTERS = {"call_expression", "field_expression", "identifier", "parenthesized_expression", "subscript_expression"}
MAX_TRIALS = 60


def finished(results: Path | None) -> set[str]:
    """The symbols an earlier sweep already recorded in `results`, so that a stopped sweep can carry on."""
    if results is None or not results.exists():
        return set()
    return {json.loads(line)["symbol"] for line in results.read_text().splitlines() if line.strip()}


def extra_grant(rows: list[dict]) -> dict | None:
    """The pointer load that spent the round-robin step retail did not: the round-robin grant before the first
    grant one step past retail's register, when that grant loads a pointer from memory."""
    first = next((i for i, row in enumerate(rows) if row["retail"] and row["retail"] != row["register"]), None)
    if first is None or rows[first]["path"] != "round-robin" or not (rows[first]["reading"] or "").startswith("one more"):
        return None
    previous = next((row for row in reversed(rows[:first]) if row["path"] == "round-robin"), None)
    return previous if previous and LOAD.fullmatch(previous["instruction"] or "") else None


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def pointer_sites(scope: Node, lines: set[int] | None = None) -> list[tuple[int, int]]:
    """Byte ranges of the expressions in `scope` used as pointers: what `->` reads through, and a macro's
    expression arguments, on `lines` (1-based) when given."""
    sites = []
    for node in _walk(scope):
        found = []
        if node.type == "field_expression" and node.child_by_field_name("operator").type == "->":
            found = [node.child_by_field_name("argument")]
        elif node.type == "call_expression" and MACRO.fullmatch(node.child_by_field_name("function").text):
            found = [a for a in node.child_by_field_name("arguments").named_children if a.type in ("call_expression", "field_expression")]
        for site in found:
            if site.type in POINTERS and (lines is None or site.start_point[0] + 1 in lines):
                sites.append((site.start_byte, site.end_byte))
    return sorted(set(sites))


def wrapped(source: bytes, site: tuple[int, int]) -> bytes:
    """`source` with the expression at `site` passed through the helper, defined after the includes above it."""
    start, end = site
    text = source[:start] + HELPER_NAME + b"(" + source[start:end] + b")" + source[end:]
    if HELPER in source:
        return text
    includes = [m.end() for m in INCLUDE.finditer(source, 0, start)]
    at = includes[-1] if includes else 0
    return text[:at] + b"\n" + HELPER + text[at:]


def costs_nothing(base, trial) -> bool:
    """No function of the unit scores lower or stops being exact under the trial."""
    for section in base.sections:
        for row in section["functions"]:
            found = trial.verdict(row["symbol"])
            if found is None or found.row["fuzzy"] < row["fuzzy"] or (row["exact"] and not found.row["exact"]):
                return False
    return True


def search(target, unit: str, symbol: str, source: bytes) -> dict:
    """Trace the function, and try the helper on each pointer near the load that spent the extra step. The
    result's `source` is the best trial that raises the function and costs no function of the unit."""
    if not regtrace.retail_registers(target.diff(target.verdict(unit, symbol)).findings):
        return {"unit": unit, "symbol": symbol, "status": "no differing register"}
    report = regtrace.run(target, unit, symbol, source)
    grant = extra_grant(report["grants"])
    result = {"unit": unit, "symbol": symbol, "before": report["score"], "after": report["score"], "status": "no extra pointer load"}
    if grant is None:
        return result
    scope = target.find_function(source, symbol)
    near = pointer_sites(scope, target.source_lines(unit, symbol, source, [grant["offset"]]))
    sites = [*near, *(site for site in pointer_sites(scope) if site not in near)][:MAX_TRIALS]
    result |= {"load": grant["instruction"], "status": "no gain", "tried": len(sites)}
    trials = {f"site{i}": wrapped(source, site) for i, site in enumerate(sites)}
    evaluations = target.evaluate(unit, {"base": source, **trials})
    base, best = evaluations["base"], None
    for name, trial in evaluations.items():
        verdict = trial.verdict(symbol) if name != "base" and not trial.error else None
        if verdict and verdict.row["fuzzy"] > result["after"] and costs_nothing(base, trial):
            best, result["after"] = name, verdict.row["fuzzy"]
            result["status"] = "exact" if verdict.row["exact"] else "improved"
    if best:
        start, end = sites[int(best.removeprefix("site"))]
        result |= {"expression": source[start:end].decode(), "source": trials[best]}
    return result
