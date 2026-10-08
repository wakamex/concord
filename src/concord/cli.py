"""concord command line.

The subcommands map to the pipeline stages: scaffold a project, seed candidates
from a decompiler, load recovered types, infer toolchain flags, run the matching
search for a function, show the attributed diff, and report progress. The stages
are wired here; the per-stage work lives in the component modules and is not yet
implemented, so each subcommand reports that it is a stub rather than doing
partial work.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from concord import __version__, knowledge, layout, oracle, results, scores
from concord.diff import diff_code, read_function
from concord.finish import finish
from concord.harvest import Harvest
from concord.lines import editable, inline_chains
from concord.model import Cause, DiffResult
from concord.order import apply_order, search_order
from concord.permute import near_misses, permute
from concord.repair import repair
from concord.search import _diff as _search_diff
from concord.search import _key, search


def _not_implemented(stage: str) -> int:
    print(f"concord {stage}: not implemented yet (skeleton)")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concord",
        description="Matching recompilation for x86-64 and C++.",
    )
    parser.add_argument("--version", action="version", version=f"concord {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_init = sub.add_parser("init", help="scaffold a project for a target binary")
    p_init.add_argument("target", help="path to the target binary")
    p_init.add_argument("--toolchain", required=True, help="pinned toolchain image id")

    p_seed = sub.add_parser("seed", help="seed candidates from a decompiler")
    p_seed.add_argument("function", help="target function name")
    p_seed.add_argument("--backend", default="ghidra", choices=["ghidra", "binaryninja"])

    p_types = sub.add_parser("types", help="load recovered class and struct layout")
    p_types.add_argument("--debug-map", help="cross-platform debug map")
    p_types.add_argument("--rtti", action="store_true", help="scan RTTI and vtables from the target")

    p_flags = sub.add_parser("flags", help="infer toolchain flags from anchor functions")
    p_flags.add_argument("anchors", nargs="+", help="anchor function names")

    p_match = sub.add_parser("match", help="search source rewrites toward a byte match for one function")
    p_match.add_argument("function", help="target function symbol")
    p_match.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_match.add_argument("--unit", required=True, help="Harvest unit, as a source path or report slug")
    p_match.add_argument("--rounds", type=int, default=4)
    p_match.add_argument("--apply", action="store_true", help="write the improved source over the unit's file")
    p_match.add_argument("--results", type=Path, default=results.LOG, help="search log to append to")

    p_permute = sub.add_parser("permute", help="random multi-step rewrite search toward a byte match for one function")
    p_permute.add_argument("function", nargs="?", help="target function symbol (with --unit)")
    p_permute.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_permute.add_argument("--unit", help="Harvest unit, as a source path or report slug")
    p_permute.add_argument(
        "--near-miss", type=float, metavar="SCORE",
        help="search every inexact function of its unit's own source scoring at least SCORE, best first",
    )  # fmt: skip
    p_permute.add_argument("--budget", type=int, default=256, help="candidate sources to compile")
    p_permute.add_argument("--batch", type=int, default=32, help="candidates compiled per step")
    p_permute.add_argument("--depth", type=int, default=3, help="most rewrites one candidate adds")
    p_permute.add_argument("--seed", type=int, default=0)
    p_permute.add_argument("--apply", action="store_true", help="write an exact match over the unit's file")
    p_permute.add_argument(
        "--apply-improved", action="store_true", help="write any improvement over the unit's file, exact or partial"
    )
    p_permute.add_argument("--results", type=Path, default=results.LOG, help="search log to append to")

    p_diff = sub.add_parser("diff", help="show the attributed diff for a function")
    p_diff.add_argument("function", help="target function symbol")
    p_diff.add_argument("--target", type=Path, help="relocatable object holding the target function")
    p_diff.add_argument("--candidate", type=Path, help="compiled object holding the candidate function")
    p_diff.add_argument("--harvest", type=Path, help="Harvest checkout whose `hv match` outputs to read")
    p_diff.add_argument("--unit", help="Harvest unit, as a source path or report slug (with --harvest)")
    p_diff.add_argument(
        "--compiler",
        help="compiler whose known idioms to show for the causes found (default: gcc-4.4.3 with --harvest)",
    )

    p_sweep = sub.add_parser("sweep", help="search definition orders of Harvest units through hv search")
    p_sweep.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_sweep.add_argument("--unit", action="append", help="unit to search (default: every unit with an inexact function)")
    p_sweep.add_argument("--budget", type=int, default=128)
    p_sweep.add_argument("--restarts", type=int, default=2)
    p_sweep.add_argument("--seed", type=int, default=0)
    p_sweep.add_argument("--apply", action="store_true", help="write verified pure-reorder gains into the sources")
    p_sweep.add_argument("--results", type=Path, default=results.LOG, help="search log to append to")

    p_rerun = sub.add_parser(
        "rerun", help="repeat every logged search that improved a function without matching it, and write the gains"
    )
    p_rerun.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_rerun.add_argument("--results", type=Path, default=results.LOG, help="search log to read and append to")
    p_rerun.add_argument("--unit", action="append", help="only this unit's functions, as a source path (repeatable)")

    p_repair = sub.add_parser(
        "repair", help="flip NaN-sense comparisons in one function where that brings it closer to the original's behavior"
    )
    p_repair.add_argument("function", help="target function symbol")
    p_repair.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_repair.add_argument("--unit", required=True, help="Harvest unit, as a source path or report slug")
    p_repair.add_argument("--base", default="master", help="git revision whose score the result must stay above")
    p_repair.add_argument("--apply", action="store_true", help="write the repaired source over the unit's file")
    p_repair.add_argument("--results", type=Path, default=results.LOG, help="search log to append to")

    p_layout = sub.add_parser(
        "layout", help="compare where the original and our compile access memory, to find misplaced fields"
    )
    p_layout.add_argument("function", nargs="?", help="target function symbol (with --unit)")
    p_layout.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_layout.add_argument("--unit", help="Harvest unit, as a source path or report slug")
    p_layout.add_argument(
        "--struct-layout", action="store_true", help="every inexact function whose diff names struct layout"
    )

    p_scores = sub.add_parser(
        "scores", help="save the progress Harvest reports upstream, or compare it with a saved snapshot"
    )
    p_scores.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_scores.add_argument("--capture", action="store_true", help="recapture Harvest's progress evidence first")
    p_scores.add_argument("--ref", help="use the evidence committed at this git revision instead of the checkout's")
    p_scores.add_argument("--save", type=Path, help="write the snapshot to this JSON file")
    p_scores.add_argument("--against", type=Path, help="compare with a snapshot saved earlier")

    p_vtables = sub.add_parser("vtables", help="compare every compiled vtable with the target's, slot by slot")
    p_vtables.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")

    p_survey = sub.add_parser("survey", help="count attributed causes over every inexact Harvest function")
    p_survey.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_survey.add_argument(
        "--lines", type=int, metavar="N", default=0,
        help="also locate each finding's source line through a -g compile, and list the N lines with the most",
    )  # fmt: skip

    sub.add_parser("status", help="report matching progress")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "diff":
        return _diff(parser, args)
    if args.command == "survey":
        return _survey(args)
    if args.command == "match":
        return _match(args)
    if args.command == "permute":
        return _permute(parser, args)
    if args.command == "sweep":
        return _sweep(args)
    if args.command == "layout":
        return _layout(parser, args)
    if args.command == "repair":
        return _repair(args)
    if args.command == "rerun":
        return _rerun(args)
    if args.command == "scores":
        return _scores(parser, args)
    if args.command == "vtables":
        return _vtables(args)
    return _not_implemented(args.command)


def _print_result(result: DiffResult, compiler: str | None) -> None:
    print(f"score {result.score:.2f}  exact {result.exact}")
    for f in result.findings:
        print(f"  {f.cause.value:22} +{f.offset:#06x}  {f.detail}")
    if not compiler or not result.findings:
        return
    idioms = knowledge.for_causes(knowledge.load(compiler), {f.cause for f in result.findings})
    if idioms:
        print(f"\nknown {compiler} idioms for these causes:")
    for i in idioms:
        print(f"  {i.id} ({i.cause.value}, {i.status}): {i.fix}")


def _diff(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if args.harvest:
        if not args.unit:
            parser.error("--harvest needs --unit")
        harvest = Harvest(args.harvest)
        _print_result(harvest.diff(harvest.verdict(args.unit, args.function)), args.compiler or "gcc-4.4.3")
    elif args.target and args.candidate:
        target = read_function(args.target, args.function)
        _print_result(diff_code(target, read_function(args.candidate, args.function)), args.compiler)
    else:
        parser.error("pass --target and --candidate, or --harvest and --unit")
    return 0


def _match(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    revision = results.commit(harvest.root)
    original = (harvest.root / "src" / harvest.source(args.unit)).read_bytes()
    result = search(harvest, args.unit, args.function, rounds=args.rounds)
    result.source, repairs = repair(harvest, harvest.source(args.unit), args.function, result.source, result.baseline.fuzzy or 0.0)
    changed = bool(result.steps or repairs)
    finished = finish(harvest, harvest.source(args.unit), args.function, result.source, original) if changed else None
    verdict = None
    if finished is not None and not result.best.exact:
        kept, verdict = oracle.gate(harvest, harvest.source(args.unit), args.function, original, finished)
        if not kept:
            print(f"the rewrite changes behavior without moving closer to the original; nothing written: {verdict}")
            finished = None
        elif verdict and "original" in verdict:
            print(f"the rewrite changes behavior toward the original: {verdict['original']}")
    results.record(
        args.results,
        {
            "command": "match",
            "harvest": revision,
            "unit": harvest.source(args.unit),
            "symbol": args.function,
            "rounds": args.rounds,
            "transforms": sorted(result.transforms),
            "reason": result.reason,
            "tried": result.tried,
            "before": result.baseline.score,
            "after": result.best.score,
            "exact": result.best.exact,
            "steps": [f"{s.rewrite.transform}: {s.rewrite.description}" for s in result.steps],
            "repairs": repairs,
            "finished": finished is not None if changed else None,
            "oracle": verdict,
            "patch": results.save_patch(args.results, harvest.source(args.unit), original, finished or result.source)
            if changed
            else None,
        },
    )
    print(f"baseline score {result.baseline.score:.2f}")
    for step in result.steps:
        print(f"  {step.rewrite.transform}: {step.rewrite.description}  -> score {step.diff.score:.2f}")
    print(f"{result.reason}: score {result.best.score:.2f}, exact {result.best.exact}, {result.tried} rewrites compiled")
    _print_result(result.best, "gcc-4.4.3")
    if changed and args.apply and finished is not None:
        path = harvest.root / "src" / harvest.source(args.unit)
        path.write_bytes(finished)
        print(f"wrote {path}")
    return 0


def _permute(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    if args.near_miss is not None:
        targets = [(unit, symbol) for unit, symbol, _ in near_misses(harvest, args.near_miss)]
        print(f"{len(targets)} functions score at least {args.near_miss}")
    elif args.unit and args.function:
        targets = [(harvest.source(args.unit), args.function)]
    else:
        parser.error("pass --unit and a function, or --near-miss")
    exact = 0
    for unit, symbol in targets:
        print(f"=== {unit} {symbol}")
        exact += _permute_one(harvest, unit, symbol, args)
    if len(targets) > 1:
        print(f"{exact} of {len(targets)} functions exact")
    return 0


def _permute_one(harvest: Harvest, source: str, symbol: str, args: argparse.Namespace) -> bool:
    revision = results.commit(harvest.root)
    original = (harvest.root / "src" / source).read_bytes()
    result = permute(harvest, source, symbol, args.budget, args.batch, args.seed, args.depth)
    result.source, repairs = repair(harvest, source, symbol, result.source, result.baseline.fuzzy or 0.0)
    improved = _key(result.best) > _key(result.baseline) or bool(repairs)
    finished = finish(harvest, source, symbol, result.source, original) if improved else None
    verdict = None
    if finished is not None and not result.best.exact:
        kept, verdict = oracle.gate(harvest, source, symbol, original, finished)
        if not kept:
            print(f"the rewrite changes behavior without moving closer to the original; nothing written: {verdict}")
            finished = None
        elif verdict and "original" in verdict:
            print(f"the rewrite changes behavior toward the original: {verdict['original']}")
    results.record(
        args.results,
        {
            "command": "permute",
            "harvest": revision,
            "unit": source,
            "symbol": symbol,
            "budget": args.budget,
            "batch": args.batch,
            "depth": args.depth,
            "seed": args.seed,
            "reason": result.reason,
            "tried": result.tried,
            "before": result.baseline.score,
            "after": result.best.score,
            "exact": result.best.exact,
            "steps": result.steps,
            "repairs": repairs,
            "finished": finished is not None if improved else None,
            "oracle": verdict,
            "patch": results.save_patch(args.results, source, original, finished or result.source) if improved else None,
        },
    )
    print(f"baseline score {result.baseline.score:.2f}")
    for step in result.steps:
        print(f"  {step}")
    for step in repairs:
        print(f"  repair {step['rewrite']}: original differences {step['original_differences']}")
    print(f"{result.reason}: score {result.best.score:.2f}, exact {result.best.exact}, {result.tried} candidates compiled")
    _print_result(result.best, "gcc-4.4.3")
    if improved and finished is None and verdict is None:
        print("its temporaries could not be given concrete types without changing the bytes; nothing written")
    elif finished is not None and (result.best.exact and args.apply or args.apply_improved):
        path = harvest.root / "src" / source
        path.write_bytes(finished)
        print(f"wrote {path}")
    return result.best.exact


def _layout(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    if args.struct_layout:
        targets = []
        for verdict in harvest.verdicts():
            if verdict.row["exact"] or verdict.symbol.startswith(("_ZTh", "_ZTv")):
                continue
            try:
                findings = harvest.diff(verdict).findings
            except (KeyError, FileNotFoundError):
                continue
            if any(f.cause == Cause.STRUCT_LAYOUT for f in findings):
                targets.append((harvest.source(verdict.unit), verdict.symbol))
    elif args.unit and args.function:
        targets = [(harvest.source(args.unit), args.function)]
    else:
        parser.error("pass --unit and a function, or --struct-layout")
    for unit, symbol in targets:
        mismatches = layout.compare(layout.accesses(harvest, symbol), layout.accesses(harvest, symbol, unit))
        print(f"=== {unit} {symbol}: {len(mismatches)} bases differ")
        for m in mismatches:
            show = lambda xs: ", ".join(f"+{o:#x}/{n} {a}" for o, n, a in xs) or "-"
            print(f"  {m.base}: original only {show(m.original)}; ours only {show(m.ours)}")
    return 0


def _repair(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    unit = harvest.source(args.unit)
    path = harvest.root / "src" / unit
    current = path.read_bytes()
    base = subprocess.run(
        ["git", "-C", str(harvest.root), "show", f"{args.base}:src/{unit}"], capture_output=True, check=True
    ).stdout
    floor = _search_diff(harvest, harvest.evaluate(unit, {"base": base})["base"], args.function).fuzzy or 0.0
    repaired, repairs = repair(harvest, unit, args.function, current, floor)
    for step in repairs:
        print(f"  {step['rewrite']}: differs from the original in {step['original_differences']['before']} -> "
              f"{step['original_differences']['after']} of {step['original_differences']['cases']} cases, score {step['fuzzy']:.2f}")  # fmt: skip
    if not repairs:
        print(f"no NaN-sense flip brings the function closer to the original while scoring above {floor:.2f} ({args.base})")
        return 0
    finished = finish(harvest, unit, args.function, repaired, current) or repaired
    results.record(
        args.results,
        {"command": "repair", "harvest": results.commit(harvest.root), "unit": unit, "symbol": args.function,
         "base": args.base, "floor": floor, "repairs": repairs,
         "patch": results.save_patch(args.results, unit, current, finished)},
    )  # fmt: skip
    if args.apply:
        path.write_bytes(finished)
        print(f"wrote {path}")
    return 0


def _rerun(args: argparse.Namespace) -> int:
    """Each function's best logged partial gain, searched again with the same command
    and settings on the current checkout, with the improvement written. The current
    transforms and layout apply, so the result is what concord produces today."""
    harvest = Harvest(args.harvest)
    best = results.partial_gains(results.load(args.results))
    if args.unit:
        best = {key: row for key, row in best.items() if key[0] in args.unit}
    print(f"{len(best)} functions with a logged partial gain")
    for (unit, symbol), row in sorted(best.items()):
        print(f"=== {row['command']} {unit} {symbol}")
        settings = argparse.Namespace(harvest=args.harvest, results=args.results, unit=unit, function=symbol)
        try:
            if row["command"] == "match":
                _match(argparse.Namespace(**vars(settings), rounds=row["rounds"], apply=True))
            else:
                options = {k: row[k] for k in ("budget", "batch", "depth", "seed")}
                _permute_one(harvest, unit, symbol, argparse.Namespace(**vars(settings), **options, apply=True, apply_improved=True))
        except (KeyError, RuntimeError) as error:
            print(f"skipped: {error}")
    return 0


def _scores(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not (args.save or args.against):
        parser.error("pass --save, --against or both")
    current = scores.snapshot(scores.report(Harvest(args.harvest), args.capture, args.ref))
    if args.save:
        args.save.write_text(json.dumps(current, sort_keys=True))
        print(f"saved {len(current['functions'])} functions and {len(current['data'])} matched data runs to {args.save}")
    if not args.against:
        return 0
    changes, lost, gained = scores.compare(json.loads(args.against.read_text()), current)

    def show(s: dict | None) -> str:
        return "absent" if s is None else ("matched" if s["matched"] else f"{s['fuzzy']:.2f}")

    for c in changes:
        print(f"{'WORSE' if c.worse else 'better':6} {show(c.before):>8} -> {show(c.after):8} {int(c.address):#x} {c.name}")
    worse = sum(c.worse for c in changes)
    newly = sum(1 for c in changes if c.after and c.after["matched"] and not (c.before and c.before["matched"]))
    print(f"{len(changes)} functions changed: {newly} newly matched, {worse} worse; data bytes matched: +{gained} -{lost}")
    return 1 if worse or lost else 0


def _sweep(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    units = args.unit or sorted(
        {v.unit for v in harvest.verdicts() if not v.row["exact"] and not v.symbol.startswith(("_ZTh", "_ZTv"))}
    )
    revision = results.commit(harvest.root)
    gained = 0
    for unit in units:
        result = search_order(harvest, unit, args.budget, args.restarts, args.seed)
        row = {
            "command": "sweep",
            "harvest": revision,
            "unit": result.unit,
            "budget": args.budget,
            "restarts": args.restarts,
            "seed": args.seed,
            "reason": result.reason,
            "gained": sorted(result.gained) if not result.lost else [],
            "patch": None,
        }
        if result.gained and not result.lost:
            original = (harvest.root / "src" / result.unit).read_bytes()
            winner = (result.run / "candidate.cpp").read_bytes()
            row["patch"] = results.save_patch(args.results, result.unit, original, winner)
        results.record(args.results, row)
        line = f"{result.unit}: {result.reason}"
        if result.gained and not result.lost:
            gained += len(result.gained)
            line += f", +{len(result.gained)} verified: {', '.join(_short_names(result.gained))}"
            if args.apply:
                apply_order(harvest, result)
                line += " (applied)"
        print(line, flush=True)
    print(f"{gained} functions gained across {len(units)} units")
    return 0


def _vtables(args: argparse.Namespace) -> int:
    compared, mismatches = Harvest(args.harvest).vtables()
    print(f"{compared} vtables compared, {len(mismatches)} differ")
    for m in mismatches:
        print(f"  {m['vtable']} slot {m['slot']}/{m['slots']}: ours {m['ours']}, target {m['target']} ({m['object']})")
    return 1 if mismatches else 0


def _short_names(symbols: set[str]) -> list[str]:
    demangled = subprocess.run(["c++filt"], input="\n".join(sorted(symbols)), capture_output=True, text=True, check=False)
    return [name.split("(")[0] for name in demangled.stdout.split("\n") if name]


def _survey(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    functions = Counter()
    primary = Counter()
    total = 0
    found = []
    for verdict in harvest.verdicts():
        if verdict.row["exact"]:
            continue
        total += 1
        try:
            result = harvest.diff(verdict)
        except (KeyError, FileNotFoundError):
            primary["(no object)"] += 1
            continue
        found.append((verdict, result))
        causes = Counter(f.cause.value for f in result.findings)
        if verdict.symbol.startswith(("_ZTh", "_ZTv")) and set(causes) == {"placement"}:
            causes = Counter({"placement (thunk)": 1})
        functions.update(causes.keys())
        primary[causes.most_common(1)[0][0] if causes else "(none)"] += 1
    print(f"{total} inexact functions")
    print(f"{'cause':24}{'functions with it':>18}{'as most frequent':>18}")
    for cause in sorted(set(functions) | set(primary), key=lambda c: -functions.get(c, 0)):
        print(f"{cause:24}{functions.get(cause, 0):>18}{primary.get(cause, 0):>18}")
    if args.lines:
        _survey_lines(harvest, found, args.lines)
    return 0


def _survey_lines(harvest: Harvest, found: list, limit: int) -> None:
    """Rank source lines by the findings whose candidate code they produced. A
    line in a header gathers the findings of every copy inlined from it."""
    units = sorted({harvest.source(v.unit) for v, _ in found})
    debug = harvest.debug_objects(units)
    by_line: dict[str, dict] = {}
    for verdict, result in found:
        obj = debug.get(harvest.source(verdict.unit))
        located = [f for f in result.findings if f.candidate is not None]
        if obj is None or not located:
            continue
        function = read_function(obj, verdict.symbol)
        chains = inline_chains(obj, function.section, [function.start + f.candidate for f in located])
        for finding, chain in zip(located, chains, strict=True):
            frame = editable(chain)
            key = str(frame) if frame else "(toolchain code)"
            entry = by_line.setdefault(key, {"causes": Counter(), "functions": set(), "units": set()})
            entry["causes"][finding.cause.value] += 1
            entry["functions"].add(verdict.symbol)
            entry["units"].add(verdict.unit)
    print(f"\nsource lines with the most located findings (of {sum(sum(e['causes'].values()) for e in by_line.values())})")
    ranked = sorted(by_line.items(), key=lambda kv: (-len(kv[1]["functions"]), -sum(kv[1]["causes"].values())))
    for key, entry in ranked[:limit]:
        causes = ", ".join(f"{c} {n}" for c, n in entry["causes"].most_common())
        print(f"  {key:56} {len(entry['functions']):>3} functions {len(entry['units']):>3} units  {causes}")


if __name__ == "__main__":
    raise SystemExit(main())
