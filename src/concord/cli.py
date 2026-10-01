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

from concord import __version__


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
    p_diff.add_argument("function", help="target function name")

    sub.add_parser("status", help="report matching progress")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return _not_implemented(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
