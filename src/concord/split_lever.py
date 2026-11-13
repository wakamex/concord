"""Measure a shared header change on every unit that includes the header, and localize it where it splits.

A change to a shared definition, such as an accessor's return type, moves every function that uses
it. When it raises some functions and lowers others, the definition as the checkout has it is right
for the losers, and the gainers were written another way in the original. Their local equivalent,
such as the member the accessor returns read directly, can then be written in each gainer alone.
On gruntz-decomp, `CState::World()` returning `CGameWorld* const&` made `PresentTitlePage` exact and
lowered three other functions; `m_world->` in place of `World()->` in `PresentTitlePage` alone
compiles the same as the reference return there and closed it with nothing else moving.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

INCLUDE = re.compile(rb"^[ \t]*#[ \t]*include[ \t]*<([^>]+)>", re.MULTILINE)
EPS = 1e-4  # scores are objdiff's floats; anything smaller is the same score


def closure(read: Callable[[str], bytes | None], start: bytes) -> set[str]:
    """Every header under include/ that a source reaches through its `#include <...>` lines.
    `read` gives a header's text by its path under include/, or None for one outside it."""
    found: set[str] = set()
    stack = [start]
    while stack:
        for match in INCLUDE.finditer(stack.pop()):
            name = match[1].decode()
            if name not in found and (text := read(name)) is not None:
                found.add(name)
                stack.append(text)
    return found


def users(root: Path, sources: dict[str, str], header: str) -> list[str]:
    """The units whose source includes `header` (a path under the checkout's include/), directly or
    through other headers. Conditional inclusion is not evaluated, so this may name a unit that
    does not use the header, which costs only a compile."""
    cache: dict[str, bytes | None] = {}

    def read(name: str) -> bytes | None:
        if name not in cache:
            path = root / "include" / name
            cache[name] = path.read_bytes() if path.is_file() else None
        return cache[name]

    wanted = str(Path(header).relative_to("include"))
    return sorted(unit for unit, source in sources.items() if wanted in closure(read, (root / source).read_bytes()))


def replace(text: bytes, pairs: list[tuple[str, str]], what: str) -> bytes:
    """`text` with every occurrence of each OLD replaced by NEW; an OLD that does not occur is an error."""
    for old, new in pairs:
        if old.encode() not in text:
            raise ValueError(f"{old!r} does not occur in {what}")
        text = text.replace(old.encode(), new.encode())
    return text


def moved(base: list[dict], variant: list[dict]) -> list[dict]:
    """The functions whose score or exactness differs between two compiles of a unit."""
    before = {row["symbol"]: row for row in base}
    rows = []
    for row in variant:
        old = before.get(row["symbol"])
        if old and (abs(row["fuzzy"] - old["fuzzy"]) > EPS or row["exact"] != old["exact"]):
            rows.append({"symbol": row["symbol"], "address": row["address"], "base": old["fuzzy"], "variant": row["fuzzy"],
                         "base_exact": old["exact"], "variant_exact": row["exact"]})  # fmt: skip
    return rows


def holds(base: list[dict], candidate: list[dict], symbol: str) -> dict:
    """Whether a local rewrite of `symbol` raises it without making a function that was exact
    inexact, and the functions it moved."""
    changes = moved(base, candidate)
    target = next((row for row in changes if row["symbol"] == symbol), None)
    broke = [row["symbol"] for row in changes if row["base_exact"] and not row["variant_exact"]]
    raised = target is not None and target["variant"] > target["base"] + EPS
    return {"symbol": symbol, "holds": raised and not broke, "score": target["variant"] if target else None,
            "exact": bool(target and target["variant_exact"]), "broke": broke, "moved": changes}  # fmt: skip


def rows(evaluation) -> list[dict]:
    if evaluation.error:
        raise RuntimeError(f"{evaluation.unit} did not compile: {evaluation.error[-2000:]}")
    return evaluation.sections[0]["functions"]


def run(target, header: str, change: list[tuple[str, str]], local: list[tuple[str, str]] | None = None,
        units: list[str] | None = None, progress: Callable[[str], None] | None = None) -> dict:  # fmt: skip
    """Compile every unit that includes `header` with and without `change` applied to it, and
    report the functions it raises and lowers. With `local`, write that rewrite into each raised
    function alone, under the unchanged header, and report which of those rewrites hold."""
    variant = replace((target.root / header).read_bytes(), change, header)
    units = [target.slug(unit) for unit in units] if units else users(target.root, target.sources, header)
    found, local_results, failed = [], [], []
    for unit in units:
        source = target.source_path(unit).read_bytes()
        try:
            base = rows(target.evaluate(unit, {"base": source})["base"])
            changed = moved(base, rows(target.evaluate(unit, {"variant": source}, {header: variant})["variant"]))
        except RuntimeError as error:
            failed.append(str(error))
            continue
        for row in changed:
            row["unit"] = unit
        found += changed
        gainers = [row["symbol"] for row in changed if row["variant"] > row["base"] + EPS]
        if local and gainers:
            candidates = {}
            for symbol in gainers:
                node = target.find_function(source, symbol)
                body = source[node.start_byte : node.end_byte]
                try:
                    candidates[symbol] = source[: node.start_byte] + replace(body, local, symbol) + source[node.end_byte :]
                except ValueError:
                    local_results.append({"symbol": symbol, "unit": unit, "holds": False, "skipped": "the local rewrite does not occur in it"})
            if candidates:
                named = {f"local{i}": text for i, text in enumerate(candidates.values())}
                evaluations = target.evaluate(unit, named)
                for (symbol, _), name in zip(candidates.items(), named, strict=True):
                    try:
                        local_results.append({"unit": unit, **holds(base, rows(evaluations[name]), symbol)})
                    except RuntimeError as error:
                        local_results.append({"symbol": symbol, "unit": unit, "holds": False, "skipped": str(error)})
        if progress:
            progress(unit)
    return {
        "header": header,
        "change": change,
        "local": local or [],
        "units": units,
        "failed": failed,
        "raised": [row for row in found if row["variant"] > row["base"]],
        "lowered": [row for row in found if row["variant"] < row["base"]],
        "split": any(row["variant"] > row["base"] for row in found) and any(row["variant"] < row["base"] for row in found),
        "localized": local_results,
    }
