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
from collections import Counter
from pathlib import Path

from concord import __version__, knowledge
from concord.diff import diff_code, read_function
from concord.harvest import Harvest
from concord.model import DiffResult


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

    p_match = sub.add_parser("match", help="run the matching search for a function")
    p_match.add_argument("function", help="target function name")
    p_match.add_argument("--max-seconds", type=float, default=600.0)

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

    p_survey = sub.add_parser("survey", help="count attributed causes over every inexact Harvest function")
    p_survey.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")

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


def _survey(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    functions = Counter()
    primary = Counter()
    total = 0
    for verdict in harvest.verdicts():
        if verdict.row["exact"]:
            continue
        total += 1
        try:
            result = harvest.diff(verdict)
        except (KeyError, FileNotFoundError):
            primary["(no object)"] += 1
            continue
        causes = Counter(f.cause.value for f in result.findings)
        functions.update(causes.keys())
        primary[causes.most_common(1)[0][0] if causes else "(none)"] += 1
    print(f"{total} inexact functions")
    print(f"{'cause':24}{'functions with it':>18}{'as most frequent':>18}")
    for cause in sorted(set(functions) | set(primary), key=lambda c: -functions.get(c, 0)):
        print(f"{cause:24}{functions.get(cause, 0):>18}{primary.get(cause, 0):>18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
