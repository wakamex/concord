"""Find the open gruntz-decomp functions that score below their recorded high, and the changes behind each drop.

config/match_baseline.tsv keeps, per function, its current score (cur_pct) and its all-time high (hist_pct).
A function is lost when its current score is at least a gap below that high. The baseline's history shows
when it fell: the last commit where cur_pct reached the high is the reference high, and each later commit
that lowered cur_pct is a drop. Each reference high is compiled at its commit through history.at_commit,
since a recorded high can be a snapshot no build reproduces. The function's own source is compared at that
commit and now, and so are the headers its unit includes, with the functions and macros in them that the
function reaches.

Functions are followed by their RVA, not their symbol: a rename leaves the RVA in place, and the baseline
says the RVA identifies the body. A renamed symbol is compiled under its current name, so the compile pairs
with the retail object.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from concord.gruntz import Gruntz, function_at
from concord.history import at_commit, rename_words, tree_at
from concord.includes import Definition, Tree, closure, definitions, words

BASELINE = "config/match_baseline.tsv"
CALLED = re.compile(r"\b([A-Za-z_]\w*) \(|\b([A-Z][A-Z0-9_]{2,})\b")
EPSILON = 1e-9
BATCH = 200  # baseline blobs read per git call


class Row(NamedTuple):
    unit: str
    name: str
    best: float
    cur: float
    hist: float
    rva: int
    state: str


def rounded(value: float) -> float:
    """Scores at the baseline's precision: four decimals."""
    return round(value, 4)


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


def parse_baseline(text: bytes) -> dict[tuple[str, int], Row]:
    """The function rows of a baseline, by (unit, RVA). Anonymous data symbols have no RVA and are skipped."""
    rows = {}
    for line in text.decode(errors="replace").splitlines():
        fields = line.split("\t")
        if line.startswith("#") or len(fields) < 8 or not fields[6]:
            continue
        rva = int(fields[6], 16)
        rows[fields[0], rva] = Row(fields[0], fields[1], float(fields[2]), float(fields[3]), float(fields[7]), rva, fields[8] if len(fields) > 8 else "")
    return rows


def read_blobs(root: Path, specs: list[str]) -> list[bytes | None]:
    """The contents of each object spec (commit:path), or None where the path does not exist."""
    output = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input="".join(f"{spec}\n" for spec in specs).encode(),
                            capture_output=True, check=True).stdout  # fmt: skip
    blobs, at = [], 0
    for _ in specs:
        end = output.index(b"\n", at)
        header = output[at:end].split()
        if header[-1] == b"missing":
            blobs.append(None)
            at = end + 1
        else:
            size = int(header[2])
            blobs.append(output[end + 1 : end + 1 + size])
            at = end + 2 + size
    return blobs


def snapshots(root: Path, commits: list[str], units: set[str]) -> dict[str, dict[tuple[str, int], Row]]:
    """The rows of the given units in the baseline at each commit."""
    found: dict[str, dict[tuple[str, int], Row]] = {}
    for start in range(0, len(commits), BATCH):
        batch = commits[start : start + BATCH]
        for commit, blob in zip(batch, read_blobs(root, [f"{commit}:{BASELINE}" for commit in batch])):
            rows = parse_baseline(blob) if blob is not None else {}
            found[commit] = {key: row for key, row in rows.items() if key[0] in units}
    return found


@dataclass
class Episode:
    """A reference high and the drops after it, until the next commit that reaches the high."""

    high: str
    drops: list[dict] = field(default_factory=list)


def episodes(points: list[tuple[str, float]], hist: float) -> list[Episode]:
    """The high episodes of a score history: points are (commit, cur_pct) for each commit that holds the row."""
    found: list[Episode] = []
    previous = None
    for commit, cur in points:
        if rounded(cur) >= hist - EPSILON:
            found.append(Episode(commit))
        elif found and previous is not None and rounded(cur) < rounded(previous) - EPSILON:
            found[-1].drops.append({"commit": commit, "from": rounded(previous), "to": rounded(cur)})
        previous = cur
    return found


def mangled_identifier(name: str) -> str | None:
    """The identifier of a function's MSVC mangled name, such as CellFlagsAt for ?CellFlagsAt@CMapMgr@@..."""
    if name.startswith("?") and not name.startswith("??") and "@" in name:
        return name[1 : name.index("@")]
    return None


def renames(unit: str, before: dict[tuple[str, int], Row], after: dict[tuple[str, int], Row]) -> list[tuple[str, str]]:
    """The identifiers that changed between two baselines for the same RVA, when the rest of the mangled name is the same:
    the same class and signature under another name."""
    pairs = set()
    for (row_unit, rva), row in before.items():
        now = after.get((unit, rva)) if row_unit == unit else None
        if now is None or now.name == row.name:
            continue
        old, new = mangled_identifier(row.name), mangled_identifier(now.name)
        if old and new and row.name.split("@")[1:] == now.name.split("@")[1:]:
            pairs.add((old, new))
    return sorted(pairs)


def compile_at(target: Gruntz, unit: str, commit: str, pairs: list[tuple[str, str]]) -> tuple[dict[int, float], str]:
    """The unit's scores at a commit by RVA, with renamed identifiers, or the compiler's complaint."""
    try:
        (result,) = at_commit(target, unit, [commit], pairs)
    except ValueError as error:
        return {}, str(error)
    if "error" in result:
        return {}, result["error"]
    return {int(row["address"], 16): row["fuzzy"] for row in result["functions"]}, ""


class Version(NamedTuple):
    """A unit's source and headers at one commit, renamed as the compile renames them, next to the working tree."""

    before: Tree
    now: Tree
    changed: list[str]  # headers in either closure whose contents differ
    pool: dict[str, list[tuple[str, str | None, Definition]]]  # name -> (header, change, definition)


def compare_headers(before: Tree, now: Tree, source: str) -> tuple[list[str], dict]:
    """The changed headers of the unit's closure, and every definition in those headers indexed by name. A change is
    "changed", "added" or "removed"; a definition that did not change has None."""
    changed, pool = [], {}
    for path in sorted((closure(before, source) | closure(now, source)) - {source}):
        old_text, new_text = before.read(path), now.read(path)
        if old_text != new_text:
            changed.append(path)
        old_defs = definitions(old_text) if old_text is not None else {}
        new_defs = definitions(new_text) if new_text is not None else {}
        for key in old_defs.keys() | new_defs.keys():
            if key not in old_defs:
                change, definition = "added", new_defs[key]
            elif key not in new_defs:
                change, definition = "removed", old_defs[key]
            else:
                change = "changed" if old_defs[key].text != new_defs[key].text else None
                definition = new_defs[key]
            pool.setdefault(definition.name, []).append((path, change, definition))
    return changed, pool


def called(text: str) -> set[str]:
    """The names a token string calls: an identifier followed by a parenthesis, and all-caps macro names, which may
    stand for a constant. A type named only in a declaration is not a call, so its constructor is not reached."""
    return {call or macro for call, macro in CALLED.findall(text)}


def reached_changes(pool: dict, seeds: set[str]) -> list[dict]:
    """The changed definitions a function reaches: those it calls, and those its called definitions call, matched by name.
    `seeds` are the names the function calls."""
    found, visited, todo = [], set(), list(seeds)
    seen = set()
    while todo:
        name = todo.pop()
        if name in visited:
            continue
        visited.add(name)
        for path, change, definition in pool.get(name, []):
            if (path, definition.key) in seen:
                continue
            seen.add((path, definition.key))
            if change:
                found.append({"header": path, "name": definition.qualified, "kind": definition.kind, "change": change, "line": definition.line})
            todo.extend(called(definition.text))
    return sorted(found, key=lambda item: (item["header"], item["name"]))


def free_renames(pairs: list[tuple[str, str]], files: dict[str, bytes]) -> list[tuple[str, str]]:
    """The renames whose new name does not already occur in the snapshot. Renaming onto an occurring name would merge
    two functions, so such a rename is left out and its function is scored under the name it had."""
    return [(old, new) for old, new in pairs if not any(re.search(rb"\b" + new.encode() + rb"\b", text) for text in files.values())]


def version(target: Gruntz, unit: str, commit: str, files: dict[str, bytes], pairs: list[tuple[str, str]]) -> Version:
    """The unit's source and headers at a commit with the renames applied, against the working tree. A vendored SDK
    header is read from the working tree on both sides, as the compile reads it."""
    source = target.source(unit)
    files = rename_words(files, pairs, commit)
    working = Tree.working(target.root, [source])
    before = Tree(set(files) | {name for name in working.names if name.startswith("vendor/")},
                  lambda path: files[path] if path in files else (target.root / path).read_bytes())  # fmt: skip
    changed, pool = compare_headers(before, working, source)
    return Version(before, working, changed, pool)


def source_at(root: Path, commit: str, path: str) -> bytes | None:
    try:
        return git(root, "show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return None


def drop_changes(target: Gruntz, row: Row, commits: list[str], snaps: dict, drop: dict) -> bool | None:
    """Whether the function's own text changed in the drop's commit, against the commit before it, with the renames that
    commit made applied. None when either side has no RVA label."""
    position = commits.index(drop["commit"])
    if position == 0:
        return None
    before = commits[position - 1]
    source = target.source(row.unit)
    old = own_text(source_at(target.root, before, source), row.rva, row.name)
    new = own_text(source_at(target.root, drop["commit"], source), row.rva, row.name)
    if old is None or new is None:
        return None
    for old_name, new_name in renames(row.unit, snaps[before], snaps[drop["commit"]]):
        old = re.sub(rf"\b{re.escape(old_name)}\b", new_name, old)
    return old != new


def own_text(data: bytes | None, rva: int, name: str) -> str | None:
    """The function's tokens from its RVA label in a source, or None when the source has no such label."""
    if data is None:
        return None
    try:
        return words(function_at(data, rva, name), data)
    except KeyError:
        return None


class Snapshot(NamedTuple):
    """A unit at one commit, compiled and read with the same renames: its scores by RVA, and its version."""

    scores: dict[int, float]
    error: str
    version: Version


def snapshot(target: Gruntz, unit: str, commit: str, pairs: list[tuple[str, str]]) -> Snapshot:
    files = tree_at(target.root, commit, ["include", target.source(unit)])
    pairs = free_renames(pairs, files)
    scores, error = compile_at(target, unit, commit, pairs)
    return Snapshot(scores, error, version(target, unit, commit, files, pairs))


def describe(target: Gruntz, verdict, row: Row, reference: Episode | None, commits: list[str], snaps, head_units, cache: dict) -> dict:
    """One lost function: the reference high confirmed by compile, its drops, and what changed since the high."""
    record = {
        "unit": verdict.unit,
        "symbol": verdict.symbol,
        "rva": f"{row.rva:#x}",
        "bytes": verdict.row["size"],
        "now": rounded(verdict.row["fuzzy"]),
        "high": row.hist,
        "high_commit": None,
        "high_symbol": None,
        "status": "stale",
        "detail": "",
        "compiled": None,
        "drops": [],
        "own_source_changed": None,
        "changed_headers": [],
        "inline_helpers": [],
    }
    if reference is None:
        record["detail"] = "no commit in the baseline history scored the high"
        return record
    commit = reference.high
    record.update(high_commit=commit, high_symbol=snaps[commit][(row.unit, row.rva)].name)
    record["drops"] = [{**drop, "own_text_changed": drop_changes(target, row, commits, snaps, drop)} for drop in reference.drops]
    key = (row.unit, commit)
    if key not in cache:
        cache[key] = snapshot(target, row.unit, commit, renames(row.unit, snaps[commit], head_units))
    shot = cache[key]
    if shot.error:
        record["detail"] = f"compile failed at {commit[:9]}: {shot.error.strip().splitlines()[-1][:300]}" if shot.error.strip() else "compile failed"
    elif row.rva not in shot.scores:
        record["detail"] = f"the unit at {commit[:9]} does not emit it under its current name"
    else:
        record["compiled"] = rounded(shot.scores[row.rva])
        if record["compiled"] >= row.hist - EPSILON:
            record["status"] = "confirmed"
        else:
            record["detail"] = f"the compile at {commit[:9]} scores {record['compiled']:.4f}, not {row.hist:.4f}"
    old = own_text(shot.version.before.read(target.source(row.unit)), row.rva, verdict.symbol)
    new = own_text(shot.version.now.read(target.source(row.unit)), row.rva, verdict.symbol)
    if old is not None and new is not None:
        record["own_source_changed"] = old != new
        record["inline_helpers"] = reached_changes(shot.version.pool, called(old) | called(new))
    record["changed_headers"] = shot.version.changed
    return record


def run(target: Gruntz, min_gap: float = 0.5, limit: int | None = None, progress=None) -> dict:
    """Every function scoring below 100 in the checkout's last build whose high is at least `min_gap` above it,
    largest first, each described by `describe`."""
    root = target.root
    commits = git(root, "log", "--first-parent", "--reverse", "--format=%H", "HEAD", "--", BASELINE).decode().split()
    head = parse_baseline(git(root, "show", f"HEAD:{BASELINE}"))
    by_name = {(row.unit, row.name): row for row in head.values()}
    lost = []
    for verdict in target.verdicts():
        row = by_name.get((verdict.unit, verdict.symbol))
        if row is None or rounded(verdict.row["fuzzy"]) >= 100.0:
            continue
        if row.hist - rounded(verdict.row["fuzzy"]) >= min_gap - EPSILON:
            lost.append((verdict, row))
    lost.sort(key=lambda item: (-item[0].row["size"], item[0].unit, item[0].symbol))
    if limit is not None:
        lost = lost[:limit]
    units = {verdict.unit for verdict, _ in lost}
    snaps = snapshots(root, commits, units)
    head_units = {key: row for key, row in head.items() if key[0] in units}
    cache: dict = {}
    functions = []
    for index, (verdict, row) in enumerate(lost):
        if progress:
            progress(verdict.unit, index + 1, len(lost))
        key = (row.unit, row.rva)
        points = [(commit, snaps[commit][key].cur) for commit in commits if key in snaps[commit] and snaps[commit][key].state != "absent"]
        found = episodes(points, row.hist)
        functions.append(describe(target, verdict, row, found[-1] if found else None, commits, snaps, head_units, cache))
    return {"functions": functions, "summary": {**summary(functions), "reproduction": calibration(snaps, cache)}}


def calibration(snaps: dict, cache: dict) -> dict:
    """How often a compile at a reference commit reproduces the baseline's own score there, for every function of
    each compiled unit. A high is only as trustworthy as this rate, since it is the same comparison."""
    found = {"rows": 0, "emitted": 0, "exact": 0}
    for (unit, commit), shot in cache.items():
        for (row_unit, rva), row in snaps[commit].items():
            if row_unit != unit or row.state == "absent":
                continue
            found["rows"] += 1
            if rva in shot.scores:
                found["emitted"] += 1
                found["exact"] += rounded(shot.scores[rva]) == rounded(row.cur)
    return found


def summary(functions: list[dict]) -> dict:
    counts = {"lost": len(functions), "confirmed": 0, "stale": 0, "confirmed_bytes": 0, "stale_bytes": 0}
    for function in functions:
        counts[function["status"]] += 1
        counts[f"{function['status']}_bytes"] += function["bytes"]
    return counts


DROP_TEXT = {True: " (own text changed)", False: " (own text same)", None: ""}


def shorten(items: list[str], shown: int = 4) -> str:
    """The first few items and a count of the rest."""
    if not items:
        return "none"
    return ", ".join(items[:shown]) + (f" (+{len(items) - shown} more)" if len(items) > shown else "")


def table(functions: list[dict]) -> str:
    """The functions as aligned text, one row each."""
    head = ["function", "bytes", "now", "high", "high at", "status", "drops", "own source", "headers", "inline helpers"]
    rows = []
    for function in functions:
        drops = ", ".join(f"{d['commit'][:9]} {d['from']:.4f}>{d['to']:.4f}{DROP_TEXT[d['own_text_changed']]}" for d in function["drops"]) or "none in history"
        own = {True: "changed", False: "same", None: "unknown"}[function["own_source_changed"]]
        helpers = [f"{h['name']} ({Path(h['header']).name}:{h['line']}, {h['change']})" for h in function["inline_helpers"]]
        headers = [Path(path).name for path in function["changed_headers"]]
        rows.append([
            function["symbol"][:60], str(function["bytes"]), f"{function['now']:.4f}", f"{function['high']:.4f}",
            (function["high_commit"] or "none")[:9], function["status"], drops, own, shorten(headers), shorten(helpers),
        ])  # fmt: skip
    widths = [max(len(row[column]) for row in [head, *rows]) for column in range(len(head) - 1)]
    lines = ["  ".join(text.ljust(widths[i]) for i, text in enumerate(head[:-1])) + "  " + head[-1]]
    lines += ["  ".join(text.ljust(widths[i]) for i, text in enumerate(row[:-1])) + "  " + row[-1] for row in rows]
    return "\n".join(lines)
