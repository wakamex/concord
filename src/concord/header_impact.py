"""Check a shared header change against every unit that includes it, before the change is committed.

A unit is affected when its source includes the header, directly or through other headers. Each affected unit
is compiled from its source with the checkout's working tree, and each of its functions is compared with the
checkout's last report and with its banked best. A function that scored 100 in the last report and no longer scores 100
loses its exact status; one that falls below its banked best because of the change drops below it. Both are regressions.
A function already below 100 in the report is not an exact loss even when its banked best is 100, since the change did not
take that status away.
"""

from __future__ import annotations

from pathlib import Path

from concord.gruntz import Gruntz
from concord.includes import Tree, closure

EPSILON = 1e-9
REGRESSIONS = ("lost exact", "below banked best", "not emitted")


def rounded(value: float) -> float:
    return round(value, 4)


def resolve_header(root: Path, name: str) -> str:
    """A header as a path in the checkout, given either that way or relative to include/."""
    for candidate in (name, f"include/{name}"):
        if (root / candidate).is_file():
            return Path(candidate).as_posix()
    raise FileNotFoundError(f"{name} is not a file in the checkout or under include/")


def affected_units(target: Gruntz, paths: list[str]) -> list[str]:
    """The units whose include closure, read from the working tree, holds any of the headers."""
    tree = Tree.working(target.root, list(target.sources.values()))
    wanted = set(paths)
    return [unit for unit in sorted(target.sources) if closure(tree, target.sources[unit]) & wanted]


def run(target: Gruntz, headers: list[str], progress=None) -> dict:
    """Compile every unit the headers reach and compare each function with the last report."""
    paths = [resolve_header(target.root, name) for name in headers]
    units = affected_units(target, paths)
    rows, errors = [], {}
    for index, unit in enumerate(units):
        if progress:
            progress(unit, index + 1, len(units))
        evaluation = target.evaluate(unit, {"working": target.source_path(unit).read_bytes()})["working"]
        if evaluation.error:
            errors[unit] = evaluation.error.strip()[-800:]
            continue
        scores = {row["symbol"]: row["fuzzy"] for row in evaluation.sections[0]["functions"]}
        for verdict in target.verdicts(unit):
            before, best = rounded(verdict.row["fuzzy"]), rounded(verdict.row["best"])
            emitted = verdict.symbol in scores
            after = rounded(scores[verdict.symbol]) if emitted else 0.0
            flags = []
            if before >= 100.0 - EPSILON and after < 100.0 - EPSILON:
                flags.append("lost exact")
            if after < best - EPSILON and before >= best - EPSILON:
                flags.append("below banked best")
            if not emitted:
                flags.append("not emitted")
            if flags or after < before - EPSILON:
                rows.append({"unit": unit, "symbol": verdict.symbol, "bytes": verdict.row["size"], "report": before, "now": after,
                             "best": best, "flags": flags})  # fmt: skip
    regressions = [row for row in rows if set(row["flags"]) & set(REGRESSIONS)]
    return {
        "headers": paths,
        "units": units,
        "functions": rows,
        "errors": errors,
        "summary": {"units": len(units), "regressions": len(regressions), "lower": len(rows) - len(regressions), "failed_units": len(errors)},
    }


def table(functions: list[dict]) -> str:
    head = ["unit", "function", "bytes", "report", "now", "banked best", "flags"]
    rows = [[row["unit"], row["symbol"][:70], str(row["bytes"]), f"{row['report']:.4f}", f"{row['now']:.4f}", f"{row['best']:.4f}", ", ".join(row["flags"]) or "lower"]
            for row in functions]  # fmt: skip
    widths = [max(len(row[column]) for row in [head, *rows]) for column in range(len(head) - 1)]
    lines = ["  ".join(text.ljust(widths[i]) for i, text in enumerate(head[:-1])) + "  " + head[-1]]
    lines += ["  ".join(text.ljust(widths[i]) for i, text in enumerate(row[:-1])) + "  " + row[-1] for row in rows]
    return "\n".join(lines)
