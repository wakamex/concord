"""Check a shared header change against every unit that includes it, before the change is committed.

A unit is affected when its source includes the header, directly or through other headers. Each affected unit is
compiled twice through the same header overlay: once from the committed files (HEAD's source and its include/
headers) and once from the working tree. Only functions whose score differs between those two compiles are reported,
so a function that compiles differently from the last build report, for compiler-state reasons, is not reported
when nothing changed. A function that scores 100 at HEAD and not in the working tree loses its exact status. One that
falls below its banked best because of the change is flagged as below banked best. Either makes the command fail.
Vendored SDK headers under vendor/ are read from the working tree on both sides, as the compile reads them.
"""

from __future__ import annotations

from pathlib import Path

from concord.gruntz import Gruntz
from concord.history import tree_at
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


def working_files(root: Path, source: str) -> dict[str, bytes]:
    """The unit's source and include/ headers as the working tree holds them, in the shape tree_at returns."""
    files = {path.relative_to(root).as_posix(): path.read_bytes() for path in (root / "include").rglob("*") if path.is_file()}
    files[source] = (root / source).read_bytes()
    return files


def compile_side(target: Gruntz, unit: str, files: dict[str, bytes]) -> tuple[dict[str, float], str]:
    """The unit's scores by symbol, compiled from its source with the include/ headers overlaid, or the compiler's complaint."""
    headers = {path: text for path, text in files.items() if path.startswith("include/")}
    evaluation = target.evaluate(unit, {"candidate": files[target.source(unit)]}, headers)["candidate"]
    if evaluation.error:
        return {}, evaluation.error.strip()[-800:]
    return {row["symbol"]: row["fuzzy"] for row in evaluation.sections[0]["functions"]}, ""


def run(target: Gruntz, headers: list[str], progress=None) -> dict:
    """Compile every unit the headers reach at HEAD and in the working tree, and report the functions that differ."""
    paths = [resolve_header(target.root, name) for name in headers]
    units = affected_units(target, paths)
    rows, errors = [], {}
    for index, unit in enumerate(units):
        if progress:
            progress(unit, index + 1, len(units))
        source = target.source(unit)
        committed = tree_at(target.root, "HEAD", ["include", source])
        if source not in committed:
            errors[unit] = f"{source} is not committed at HEAD"
            continue
        before, head_error = compile_side(target, unit, committed)
        after, working_error = compile_side(target, unit, working_files(target.root, source))
        if head_error or working_error:
            messages = ([f"HEAD: {head_error}"] if head_error else []) + ([f"working tree: {working_error}"] if working_error else [])
            errors[unit] = "\n".join(messages)
            continue
        for verdict in target.verdicts(unit):
            if verdict.symbol not in before and verdict.symbol not in after:
                continue
            was, now = rounded(before.get(verdict.symbol, 0.0)), rounded(after.get(verdict.symbol, 0.0))
            best = rounded(verdict.row["best"])
            flags = []
            if was >= 100.0 - EPSILON and now < 100.0 - EPSILON:
                flags.append("lost exact")
            if now < best - EPSILON and was >= best - EPSILON:
                flags.append("below banked best")
            if verdict.symbol in before and verdict.symbol not in after:
                flags.append("not emitted")
            if flags or abs(now - was) > EPSILON:
                rows.append({"unit": unit, "symbol": verdict.symbol, "bytes": verdict.row["size"], "committed": was, "working": now,
                             "best": best, "flags": flags})  # fmt: skip
    regressions = [row for row in rows if set(row["flags"]) & set(REGRESSIONS)]
    lower = [row for row in rows if row not in regressions and row["working"] < row["committed"]]
    return {
        "headers": paths,
        "units": units,
        "functions": rows,
        "errors": errors,
        "summary": {"units": len(units), "regressions": len(regressions), "lower": len(lower),
                    "higher": len(rows) - len(regressions) - len(lower), "failed_units": len(errors)},
    }


def table(functions: list[dict]) -> str:
    head = ["unit", "function", "bytes", "committed", "working", "banked best", "flags"]
    rows = [[row["unit"], row["symbol"][:70], str(row["bytes"]), f"{row['committed']:.4f}", f"{row['working']:.4f}", f"{row['best']:.4f}",
             ", ".join(row["flags"]) or ("lower" if row["working"] < row["committed"] else "higher")]
            for row in functions]  # fmt: skip
    widths = [max(len(row[column]) for row in [head, *rows]) for column in range(len(head) - 1)]
    lines = ["  ".join(text.ljust(widths[i]) for i, text in enumerate(head[:-1])) + "  " + head[-1]]
    lines += ["  ".join(text.ljust(widths[i]) for i, text in enumerate(row[:-1])) + "  " + row[-1] for row in rows]
    return "\n".join(lines)
