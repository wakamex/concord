"""concord command line: one subcommand per tool, each implemented in the module of its name."""

from __future__ import annotations

import argparse
import json
import random
import shlex
import subprocess
from collections import Counter
from pathlib import Path

from concord import (
    __version__,
    authored,
    bench,
    blockers,
    calls,
    calls_constants,
    causal,
    census,
    comparison_flips,
    destructors,
    enumswitch,
    flag_audit,
    frames,
    initialization,
    inline_budget,
    inlines,
    knowledge,
    layout,
    oracle,
    place,
    residual,
    results,
    scores,
    scratch,
    signedness,
    statics,
    symbol_audit,
    symbol_table,
)
from concord.complete import TYPES, absent, complete, settle, state
from concord.diff import diff_code, read_function
from concord.finish import finish
from concord.harvest import BUILD, Harvest
from concord.irrlicht import helper_names, name_helpers
from concord.lines import editable, inline_chains
from concord.model import Cause, DiffResult
from concord.order import apply_order, measure_placements, search_order
from concord.permute import near_misses, permute
from concord.phase import PHASES, scan, spans
from concord.repair import repair
from concord.search import _diff as _search_diff
from concord.search import _key, search
from concord.seed import (
    _bring_includes,
    header,
    helper_taken,
    keep_port_linking,
    replace_in_port,
    restore_vtable_overrides,
    seed,
)
from concord.unguard import definitions, irrlicht_definitions, unguard, without_guard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="concord",
        description="Matching recompilation for x86-64 and C++.",
    )
    parser.add_argument("--version", action="version", version=f"concord {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_seed = sub.add_parser("seed", help="seed daisy units from Irrlicht 0.7 and place them in the target")
    p_seed.add_argument("units", nargs="+", help="units to seed, as paths under src/ (daisy/.../File.cpp)")
    p_seed.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_seed.add_argument(
        "--complete",
        action="store_true",
        help="add Irrlicht's members to Harvest's ox core headers when a unit needs them "
        "(can change code in other units; check the whole build with concord scores)",
    )

    p_seed.add_argument(
        "--stub",
        action="store_true",
        help="give a function whose body does not compile a body that traps, so the rest of the unit can match",
    )
    p_seed.add_argument(
        "--allow-worse",
        action="append",
        default=[],
        metavar="SYMBOL",
        help="with --complete, accept a completion that makes this function of another unit worse (repeatable)",
    )

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
    p_permute.add_argument(
        "--skip-logged", action="store_true", help="skip functions the results log shows searched with these settings"
    )
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

    p_placement = sub.add_parser("definition-placement", help="measure one definition's positions against reference source")
    p_placement.add_argument("--harvest", type=Path, required=True)
    p_placement.add_argument("--unit", required=True)
    p_placement.add_argument("--method", required=True)
    p_placement.add_argument("--reference-source", type=Path, required=True)
    p_placement.add_argument("--budget", type=int, default=72)
    p_placement.add_argument("--save", type=Path, required=True)

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
    p_scores.add_argument("--base", help="compare source and configuration effects in an isolated worktree")
    p_scores.add_argument("--ref", help="use the evidence committed at this git revision instead of the checkout's")
    p_scores.add_argument("--save", type=Path, help="write the snapshot to this JSON file")
    p_scores.add_argument("--against", type=Path, help="compare with a snapshot saved earlier")

    p_flags = sub.add_parser("flag-audit", help="measure flag variations on unchanged units")
    p_flags.add_argument("--harvest", type=Path, required=True, help="Harvest checkout after hv match")
    p_flags.add_argument("--unit", action="append", required=True, help="source path under src/ (repeatable)")
    p_flags.add_argument("--variant", action="append", required=True, metavar="NAME=FLAGS", help="named extra flags (repeatable)")
    p_flags.add_argument("--save", type=Path, required=True, help="write scores and exact-function changes as JSON")

    p_audit = sub.add_parser("symbols-check", help="audit weak symbol names using emitted copies and target return-value evidence")
    p_audit.add_argument('--harvest', type=Path, required=True)
    p_audit.add_argument('--evidence', action='append', help="evidence kind to check (repeatable; default: manual)")
    p_audit.add_argument('--save', type=Path, help="write structured evidence")

    p_merge = sub.add_parser("symbols-merge", help="three-way merge symbol tables, refusing row and address conflicts")
    for side in ('base', 'ours', 'theirs'):
        p_merge.add_argument('--' + side, type=Path, required=True)
    p_merge.add_argument('--output', type=Path, required=True)

    p_phase = sub.add_parser(
        "phase", help="find functions whose code depends on the number of declarations before them"
    )
    p_phase.add_argument("units", nargs="*", help="units to scan (default: every unit hv match built)")
    p_phase.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_phase.add_argument("--phases", type=int, default=PHASES, help="extra declaration counts to try, from 0")
    p_phase.add_argument("--save", type=Path, help="write every function's exact phases to this JSON file")

    p_limits = sub.add_parser(
        "limits", help="compile the unchanged unit under lowered inliner limits and see what becomes exact"
    )
    p_limits.add_argument("units", nargs="*", help="units to scan (default: every unit hv match built)")
    p_limits.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_limits.add_argument("--save", type=Path, help="write every function's exact settings to this JSON file")

    p_complete = sub.add_parser(
        "complete", help="add Irrlicht members to a partial ox core header, if the units including it are no worse"
    )
    p_complete.add_argument("members", nargs="*", help="TYPE.member, such as CMatrix4.transformVect")
    p_complete.add_argument(
        "--fill",
        nargs=2,
        metavar=("TYPE", "UNIT"),
        help="add TYPE's remaining Irrlicht members one at a time until UNIT's functions that depend on the "
        "declaration count (concord phase) are exact again",
    )
    p_complete.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_complete.add_argument("--allow-worse", action="append", default=[], metavar="SYMBOL")

    p_header = sub.add_parser("header", help="translate an Irrlicht header Harvest lacks to the path the Mac debug map records")
    p_header.add_argument("names", nargs="+", help="Irrlicht header file names, such as ITriangleSelector.h")
    p_header.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")

    p_unguard = sub.add_parser(
        "unguard", help="expose what a Harvest header keeps behind HARVEST_PORT to the matching build"
    )
    p_unguard.add_argument("header", help="header under src/, such as ox/scene/ISceneNode.h")
    p_unguard.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_unguard.add_argument("--class", dest="cls", help="class whose port definitions become inline (default: the header's name)")
    p_unguard.add_argument("--port", help="port source under the checkout that defines the class's members")
    p_unguard.add_argument("--include", action="append", default=[], help="header the inline bodies need (repeatable)")
    p_unguard.add_argument("--base", metavar="REV", help="start from the header at this git revision instead of the working file")
    p_unguard.add_argument("--irrlicht", help="Irrlicht header under third_party/irrlicht-0.7 that defines the class inline")
    p_unguard.add_argument(
        "--unit", help="unit that emits the class's members; with --irrlicht, each member takes Irrlicht's body when it scores no lower there"
    )

    p_bench = sub.add_parser(
        "bench", help="perturb functions that match and count how often the searches return them to exact"
    )
    p_bench.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_bench.add_argument("--functions", type=int, default=40, help="exact functions to sample")
    p_bench.add_argument("--per-kind", type=int, help="most cases to keep of each kind of change")
    p_bench.add_argument("--budget", type=int, default=128, help="candidate sources permute may compile per case")
    p_bench.add_argument("--seed", type=int, default=0)
    p_bench.add_argument("--two-edits", type=int, default=0, metavar="N", help="also take up to N cases two changes away from a match")
    p_bench.add_argument("--undirected", action="store_true", help="run permute without its single-edit stage and location ordering")
    p_bench.add_argument(
        "--locate",
        action="store_true",
        help="instead of searching, report whether each case's diff points at the line that was changed",
    )
    p_bench.add_argument("--save", type=Path, help="write every case and its outcome to this JSON Lines file")

    p_blockers = sub.add_parser("blockers", help="why each stubbed method of seeded units still traps, grouped by cause")
    p_blockers.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_blockers.add_argument("units", nargs="*", help="units to examine (default: every unit whose source has a stub)")
    p_blockers.add_argument("--apply", action="store_true", help="restore compiling stub bodies in place; verify matching separately")
    p_blockers.add_argument("--only", action="append", metavar="METHOD", help="examine only this declarator name, such as Class::method (repeatable)")

    p_resize = sub.add_parser('resize-forms', help="measure vector resize initialization call shapes without changing source")
    p_resize.add_argument('--harvest', type=Path, required=True)
    p_resize.add_argument('--unit', required=True)
    p_resize.add_argument('--method', required=True)
    p_resize.add_argument('--member', required=True)
    p_resize.add_argument('--element-type', required=True)
    p_resize.add_argument('--restore', action='store_true', help="use the translated ancestor body in place first")
    p_resize.add_argument('--save', type=Path)

    p_refresh = sub.add_parser("refresh", help="refresh selected seeded method bodies from current Irrlicht translation")
    p_refresh.add_argument('--harvest', type=Path, required=True)
    p_refresh.add_argument('--unit', required=True)
    p_refresh.add_argument('--header', help="refresh inline bodies in this header, compiling --unit")
    p_refresh.add_argument('methods', nargs='+', help="declarator names such as Class::method")
    p_refresh.add_argument('--companion-first', action='store_true', help="place the companion header before generated quoted includes")
    p_refresh.add_argument('--apply', action='store_true', help="write compiling refreshed bodies; gate matches separately")

    p_rename = sub.add_parser(
        "rename", help="give each file-local helper in a unit the plain name a person would write"
    )
    p_rename.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_rename.add_argument("units", nargs="+", help="units under src/, such as daisy/scene/CXAnimationPlayer.cpp")

    p_calls = sub.add_parser("learn-calls", help="prove unknown callee addresses from aligned direct calls")
    p_calls.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_calls.add_argument("units", nargs="*", help="units whose callees may be named (calls checked across every match report)")
    p_calls.add_argument("--apply", action="store_true", help="save proven addresses using Harvest's symbol writer")
    p_calls.add_argument("--save", type=Path, help="save all aligned call sites and accepted addresses as JSON")
    p_calls.add_argument("--windows", action="store_true", help="also prove addresses from globally unique byte-exact call blocks")
    p_calls.add_argument("--object", type=Path, help="use one compiled candidate instead of its canonical object")
    p_calls.add_argument("--constructor-aliases", action="store_true", help="instead reconcile named constructor copies whose exact code and relocations are identical")

    p_author = sub.add_parser("author", help="measure registered authored source, then write a verified candidate")
    p_author.add_argument("function", nargs="?", help="target function symbol")
    p_author.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_author.add_argument("--unit", help="Harvest unit, as a source path or report slug")
    p_author.add_argument("--apply", action="store_true", help="write an accepted candidate to its source file")
    p_author.add_argument("--seed-manifest", type=Path, help="JSON with files, units and optional extent claims for absent authored units")
    p_author.add_argument("--overlay-manifest", type=Path, help="JSON files mapping for an authored header/layout correction")
    p_author.add_argument("--save", type=Path, help="save authored seed measurements")
    p_author.add_argument("--variant", help="select a registered authored header spelling")
    p_author.add_argument("--candidate", type=Path, help="authored unit source changing only this function body")
    p_author.add_argument("--source-candidate", type=Path,
                          help="authored source candidate with reordered functions or file-scope wide-string pointer tables")
    p_author.add_argument("--results", type=Path, default=results.LOG, help="search log to append to")

    p_enum_switch = sub.add_parser(
        "enum-switch", help="find enum dispatches whose target keeps a range check the build omits"
    )
    p_enum_switch.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_enum_switch.add_argument("--unit", help="limit the scan to one Harvest unit")
    p_enum_switch.add_argument("--function", help="limit the scan to one target function symbol")
    p_enum_switch.add_argument("--apply", action="store_true", help="write candidates accepted through author")

    p_place = sub.add_parser("place", help="learn references and place called templates without re-seeding source")
    p_place.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_place.add_argument("units", nargs="+", help="units to refresh")
    p_place.add_argument("--restore-literals", action="store_true", help="try ancestor string literals, keeping only exact target matches")
    p_place.add_argument("--save", type=Path, help="save placement results as JSON")
    p_place.add_argument("--object", type=Path, help="learn addresses from one compiled candidate, then rebuild unchanged source")

    p_extent = sub.add_parser("place-extent", help="name a target function extent proved by an exact candidate body")
    p_extent.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_extent.add_argument("--unit", required=True, help="compiled source unit containing the candidate function")
    p_extent.add_argument("--symbol", required=True, help="candidate function symbol as emitted in the unit object")
    p_extent.add_argument("--address", required=True, type=lambda value: int(value, 0),
                          help="target function address, in decimal or hexadecimal")

    p_place_bss = sub.add_parser("place-bss", help="place every unplaced unit .bss proved by its static initializer")
    p_place_bss.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_place_bss.add_argument("--save", type=Path, help="save the placements as JSON")

    p_clean = sub.add_parser("clean", help="remove concord's old work directories from the Harvest checkout")
    p_clean.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_clean.add_argument("--older-than", type=float, default=24.0, metavar="HOURS", help="keep directories changed more recently")

    p_vtables = sub.add_parser("vtables", help="compare every compiled vtable with the target's, slot by slot")
    p_vtables.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")

    p_overrides = sub.add_parser(
        "overrides", help="declare and define the members a unit's Mac vtables prove its classes had"
    )
    p_overrides.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_overrides.add_argument("units", nargs="+", help="units to restore, as source paths or report slugs")
    p_overrides.add_argument("--apply", action="store_true", help="write the verified declarations and definitions")

    p_frames = sub.add_parser("frames", help="attribute target stack-slot constraints to compiler partitions and source values")
    p_frames.add_argument("--harvest", type=Path, required=True)
    p_frames.add_argument("--census", action="store_true", help="count named size- and call-identical near misses")
    p_frames.add_argument("--unit")
    p_frames.add_argument("--symbol")
    p_frames.add_argument("--dumps", type=Path, help="verified dump directory collected by causal")
    p_frames.add_argument("--probe-constructor", help="test implicit and scoped spellings of this explicit argument type")
    p_frames.add_argument("--call", help="qualified call containing the argument")
    p_frames.add_argument("--argument", type=int, default=0)
    p_frames.add_argument("--apply", action="store_true", help="keep an exact function with no lower emitted unit scores; gate the whole build separately")
    p_frames.add_argument("--save", type=Path, required=True)

    p_budget = sub.add_parser("inline-budget", help="explain GCC IPA inline estimates and edge decisions from a compiler dump")
    p_budget.add_argument("dump", type=Path)
    p_budget.add_argument("--against", type=Path, help="compare against an unchanged-source dump")
    p_budget.add_argument("--save", type=Path)
    p_budget.add_argument("--params-def", type=Path, help="pinned GCC params.def for integer unit and function growth budgets")
    p_budget.add_argument("--harvest", type=Path, help="also compare CString literal sites in compiled objects")
    p_budget.add_argument("--unit", help="unit for the literal-site comparison")
    p_budget.add_argument("--symbol", action="append", default=[], help="function to compare (repeatable)")
    p_budget.add_argument("--probe-constructions", action="store_true", help="screen named temporary forms at mismatched literal sites as overlays")
    p_budget.add_argument("--apply", action="store_true", help="apply an eligible construction probe through author and its oracle")

    p_inlines = sub.add_parser(
        "inlines", help="compare a unit's out-of-line inline and template copies with the target's"
    )
    p_inlines.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_inlines.add_argument("units", nargs="*", help="units to check (default: every unit hv match built)")
    p_inlines.add_argument("--semantic", action="store_true", help="list Irrlicht-backed functions beyond the CString/fast_atof inline-copy budget")
    p_inlines.add_argument("--save", type=Path, help="write every unit's findings as JSON")

    p_cc = sub.add_parser(
        "calls-constants",
        help="compare every inexact function's callees, immediates and data references with the target's",
    )
    p_cc.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_cc.add_argument("units", nargs="*", help="units to check (default: every unit hv match built)")
    p_cc.add_argument("--save", type=Path, help="write the ranked findings as JSON")

    p_statics = sub.add_parser(
        "statics", help="map static objects to target sections from aligned data references"
    )
    p_statics.add_argument("--harvest", type=Path, required=True, help="Harvest checkout (after hv match)")
    p_statics.add_argument("units", nargs="+", help="units to analyze")
    p_statics.add_argument("--save", type=Path, help="write object mappings and layout proposals as JSON")

    p_destructors = sub.add_parser("destructors", help="compare destructor definition forms as read-only header overlays")
    p_destructors.add_argument("--harvest", type=Path, required=True)
    p_destructors.add_argument("--unit", required=True)
    p_destructors.add_argument("--header", required=True)
    p_destructors.add_argument("--class", dest="cls", required=True)
    p_destructors.add_argument("--save", type=Path)

    p_causal = sub.add_parser("causal", help="trace differing instructions through pinned GCC dumps")
    p_causal.add_argument("--harvest", type=Path, required=True)
    p_causal.add_argument("--unit", required=True)
    p_causal.add_argument("symbol")
    p_causal.add_argument("--dumps", type=Path, help="reuse a dump directory previously collected for this unit")
    p_causal.add_argument("--limit", type=int, default=8, help="maximum differing instructions to trace")
    p_causal.add_argument("--offset", type=lambda value: int(value, 0), action="append", help="trace this target byte offset (repeatable, accepts 0x)")
    p_causal.add_argument("--save", type=Path, help="write structured findings and compiler metadata")
    p_causal.add_argument("--search", action="store_true", help="measure caller rewrites at traced operand origins")
    p_causal.add_argument("--budget", type=int, default=64, help="maximum caller overlays to measure")
    p_causal.add_argument("--apply", action="store_true", help="apply a measured improvement through author (requires --search)")

    p_census = sub.add_parser("causal-census", help="rank source constructs behind near-exact functions")
    p_census.add_argument("--harvest", type=Path, required=True)
    p_census.add_argument("--min-score", type=float, default=97)
    p_census.add_argument("--maximum", type=int, help="analyze at most this many functions, largest first")
    p_census.add_argument("--reuse", type=Path, help="reuse validated dump directories from an earlier census")
    p_census.add_argument("--save", type=Path, required=True)

    p_trial = sub.add_parser("causal-trial", help="measure header counterfactuals")
    p_trial.add_argument("--harvest", type=Path, required=True)
    p_trial.add_argument("--header", required=True, help="checkout-relative header path, including src/")
    trial_source = p_trial.add_mutually_exclusive_group(required=True)
    trial_source.add_argument("--line", type=int, help="line with comparison or loop-form rewrites")
    trial_source.add_argument("--authored", nargs=2, metavar=("UNIT", "SYMBOL"), help="registered authored header-function correction")
    p_trial.add_argument("--variant", help="measure only this authored header spelling")
    p_trial.add_argument("--unit", action="append", help="measure these including units first (default: every including unit)")
    p_trial.add_argument("--save", type=Path, required=True)

    p_scratch = sub.add_parser("scratch-history", help="trace actual pinned-compiler scratch searches, including failures")
    p_scratch.add_argument("--harvest", type=Path, required=True)
    p_scratch.add_argument("--unit", required=True)
    p_scratch.add_argument("--symbol", action="append", default=[])
    p_scratch.add_argument("--directory", type=Path, help="reuse an unchanged collected trace")
    p_scratch.add_argument("--reference", type=Path, help="compare an earlier structured report")
    p_scratch.add_argument("--save", type=Path, required=True)

    p_survey = sub.add_parser("survey", help="count attributed causes over every inexact Harvest function")
    p_survey.add_argument("--harvest", type=Path, required=True, help="Harvest checkout")
    p_survey.add_argument(
        "--lines", type=int, metavar="N", default=0,
        help="also locate each finding's source line through a -g compile, and list the N lines with the most",
    )  # fmt: skip

    p_comparison_flips = sub.add_parser(
        "comparison-flips", help="list inexact functions differing only by reversed comparisons"
    )
    p_comparison_flips.add_argument("--harvest", type=Path, required=True, help="Harvest checkout after `hv match`")
    p_comparison_flips.add_argument("--save", type=Path, help="write the census as JSON")

    p_signedness = sub.add_parser(
        "signedness", help="rank signedness-related instruction pairs in inexact functions"
    )
    p_signedness.add_argument("--harvest", type=Path, required=True, help="Harvest checkout after `hv match`")
    p_signedness.add_argument("--unit", help="limit the scan to one Harvest unit")
    p_signedness.add_argument("--save", type=Path, help="write the census as JSON")

    p_residual = sub.add_parser(
        "residual", help="classify the remaining differences in every inexact Harvest function"
    )
    p_residual.add_argument("--harvest", type=Path, required=True, help="Harvest checkout after `hv match`")
    p_residual.add_argument("--save", type=Path, help="write the census as JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "seed":
        return _seed(args)
    if args.command == "flag-audit":
        variants = {}
        for specification in args.variant:
            name, separator, flags = specification.partition("=")
            if not separator or not name or name == "baseline" or name in variants:
                parser.error("each --variant needs a unique NAME=FLAGS, with baseline reserved")
            variants[name] = tuple(shlex.split(flags))
        report = flag_audit.audit(Harvest(args.harvest), args.unit, variants)
        args.save.write_text(json.dumps(report, indent=2) + "\n")
        failed = False
        for unit in report["units"]:
            for row in unit["variants"]:
                if "error" in row:
                    print(f"{unit['unit']} {row['name']}: {row['error']}")
                    failed = True
                else:
                    print(f"{unit['unit']} {row['name']}: {row['exact']} exact, "
                          f"{len(row['lost_exact'])} lost, {len(row['gained_exact'])} gained")
                    if not row["scores"]:
                        print(f"{unit['unit']} {row['name']}: scores unavailable")
                        failed = True
        return int(failed)
    if args.command == "diff":
        return _diff(parser, args)
    if args.command == "scratch-history":
        harvest = Harvest(args.harvest)
        directory = args.directory or scratch.collect(harvest, args.unit)
        report = scratch.report(harvest, args.unit, directory, args.symbol, args.reference)
        args.save.write_text(json.dumps(report, indent=2) + "\n")
        print(f"{report['searches']} actual scratch searches, {report['failed_searches']} failed resets")
        for event in report['targets']:
            print(f"{event['function']} uid {event['uid']}: {event['before']} -> {event['after']}, {event['register']}; last reset {event['last_reset']}")
        return 0
    if args.command == "survey":
        return _survey(args)
    if args.command == "comparison-flips":
        report = comparison_flips.report(Harvest(args.harvest))
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        summary = report["summary"]
        print(
            f"{summary['functions']} distinct target functions, {summary['bytes']:,} target bytes; "
            f"{summary['single_flip_functions']} have exactly one flip across "
            f"{summary['unit_occurrences']} unit reports"
        )
        print(f"Scores use {report['score_kind']}.")
        symbols = sorted({row["symbol"] for row in report["functions"]})
        names = dict(zip(symbols, _short_names(set(symbols)), strict=True)) if symbols else {}
        for row in report["functions"]:
            score = f"{row['score']:.5f}%" if row["score"] is not None else "unknown"
            copies = f" reported in {len(row['units'])} units" if len(row["units"]) > 1 else ""
            print(f"{row['unit']}:{names[row['symbol']]} size {row['size']} score {score}{copies}")
            for flip in row["flips"]:
                offsets = ", ".join(f"+{offset:#x}" for offset in flip["differing_offsets"])
                inline = flip["inlined_function"] or "inline source unavailable"
                source = flip["source"]
                location = f" at {source['path']}:{source['line']}" if source else ""
                units = f" in {len(flip['units'])} units" if len(flip["units"]) > 1 else ""
                print(
                    f"  cmp +{flip['comparison_offset']:#x}, jump +{flip['jump_offset']:#x}; "
                    f"bytes {offsets}; {inline}{location}{units}"
                )
        print("Ten largest:")
        for row in report["functions"][:10]:
            print(f"  {row['unit']}:{names[row['symbol']]} size {row['size']} score {row['score']}")
            for flip in row["flips"]:
                source = flip["source"]
                location = f" at {source['path']}:{source['line']}" if source else ""
                print(f"    {flip['inlined_function'] or 'inline source unavailable'}{location}")
        return 0
    if args.command == "signedness":
        report = signedness.report(Harvest(args.harvest), args.unit)
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        summary = report["summary"]
        symbols = {row["symbol"] for row in report["functions"]}
        names = dict(zip(sorted(symbols), _short_names(symbols), strict=True)) if symbols else {}
        print(
            f"{summary['functions']} inexact functions contain {summary['pairs']} aligned signedness pairs; "
            f"{summary['only_signedness']} differ only by signedness"
        )
        for row in report["functions"]:
            score = f"{row['score']:.2f}"
            print(
                f"{row['unit']}:{names[row['symbol']]} size {row['size']} bytes, score {score}, "
                f"signedness explains {row['explained_instruction_slots']}/{row['differing_instruction_slots']} "
                f"differing instruction slots ({row['explanation']:.1%})"
            )
            for pair in row["pairs"]:
                target_offsets = ", ".join(f"+{offset:#x}" for offset in pair["differing_target_offsets"])
                candidate_offsets = ", ".join(f"+{offset:#x}" for offset in pair["differing_candidate_offsets"])
                source = pair["source"]
                location = f"{source['path']}:{source['line']}" if source else "source line unavailable"
                target_text = "; ".join(pair["target"])
                candidate_text = "; ".join(pair["candidate"])
                print(
                    f"  {pair['kind']} target +{pair['target_offsets'][0]:#x} "
                    f"candidate +{pair['candidate_offsets'][0]:#x}: {target_text} vs {candidate_text}; "
                    f"changed bytes target [{target_offsets}] candidate [{candidate_offsets}]; {location}"
                )
        return 0
    if args.command == "residual":
        print("Comparing calls, constants and data references across the build.", flush=True)
        report = residual.report(Harvest(args.harvest))
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        summary = report["summary"]
        reconciliation = report.get("progress_reconciliation")
        if reconciliation is None:
            print(
                f"{summary['functions']} distinct target functions in {summary['unit_occurrences']} inexact unit reports, "
                f"{summary['bytes']:,} target bytes"
            )
        else:
            print(
                f"{summary['functions']} progress-inexact target functions, {summary['bytes']:,} function bytes, "
                f"{summary['inexact_code_bytes']:,} inexact executable bytes"
            )
            print(
                f"residual instruction analysis: {summary['residual_analyzed_functions']} functions in "
                f"{summary['unit_occurrences']} unit occurrences, {summary['residual_analyzed_bytes']:,} bytes"
            )
        group_labels = [
            ("no-source-change", "no-source-change kinds only"),
            ("source-pointing", "source-pointing"),
            ("other", "other"),
        ]
        if "progress-missing" in summary["groups"]:
            group_labels.append(("progress-missing", "missing from residual analysis"))
        for name, label in group_labels:
            group = summary["groups"][name]
            print(f"{label}: {group['functions']} functions, {group['bytes']:,} target bytes")
        if reconciliation is not None:
            progress = reconciliation["summary"]
            print("Progress reconciliation:")
            print(
                f"  hv.progress: {progress['progress_matched_functions']}/"
                f"{progress['progress_functions']} exact functions; "
                f"{progress['progress_inexact_functions']} inexact functions, "
                f"{progress['progress_inexact_function_bytes']:,} function bytes, "
                f"{progress['unclaimed_code_bytes']:,} unclaimed code bytes, "
                f"{progress['inexact_code_bytes']:,} total inexact code bytes"
            )
            print(
                f"  residual aligned {progress['residual_progress_inexact_functions']} inexact functions "
                f"({progress['residual_progress_inexact_bytes']:,} bytes); "
                f"{progress['residual_progress_exact_functions']} residual entries are exact in progress "
                f"({progress['residual_progress_exact_bytes']:,} bytes)"
            )
            print(
                f"  missing from residual: {progress['missing_inexact_functions']} inexact functions, "
                f"{progress['missing_inexact_function_bytes']:,} bytes"
            )
            print(
                f"  code total: {progress['residual_progress_inexact_bytes']:,} aligned + "
                f"{progress['missing_inexact_function_bytes']:,} missing + "
                f"{progress['unclaimed_code_bytes']:,} unclaimed = "
                f"{progress['reconciled_code_bytes']:,} bytes"
            )
            for cause, group in reconciliation["groups"].items():
                if not group["functions"] and not group["bytes"]:
                    continue
                print(f"  {cause}: {len(group['functions'])} functions, {group['bytes']:,} bytes")
                print(f"    {group['why']}")
                print(f"    Recommendation: {group['recommendation']}")
                for function in group["functions"]:
                    evidence = "; ".join(function["evidence"])
                    candidates = ", ".join(function["candidate_units"])
                    candidate_note = f"; candidate units {candidates}" if candidates else ""
                    print(
                        f"    {function['address']} {function['symbol']} size {function['size']} bytes; "
                        f"{evidence}{candidate_note}"
                    )
            if reconciliation["exact_overlaps"]:
                print("  Residual entries already exact in progress:")
                for function in reconciliation["exact_overlaps"]:
                    print(
                        f"    {function['address']} {function['symbol']} size {function['size']} bytes; "
                        f"exact in {function['progress_source_path']}; residual units "
                        f"{', '.join(function['residual_units'])}"
                    )
            if progress["untracked_residual_functions"]:
                print(
                    f"  outside hv.progress inventory: {progress['untracked_residual_functions']} residual functions, "
                    f"{progress['untracked_residual_bytes']:,} bytes"
                )
        print("Differing instruction rows by kind:")
        for kind, counts in summary["kinds"].items():
            print(f"  {kind}: {counts['functions']} functions, {counts['rows']} rows, {counts['bytes']:,} target bytes")
        symbols = {row["symbol"] for row in report["top_source_pointing"]}
        names = dict(zip(sorted(symbols), _short_names(symbols), strict=True)) if symbols else {}
        print("Twenty largest source-pointing functions:")
        for row in report["top_source_pointing"]:
            score = f"{row['score']:.2f}%" if row["score"] is not None else "unknown"
            kinds = ", ".join(f"{item['kind']} ({item['rows']} rows)" for item in row["kinds"])
            signals = f"; signals {', '.join(row['source_signals'])}" if row["source_signals"] else ""
            print(
                f"  {row['units'][0]}:{names[row['symbol']]} size {row['size']} bytes, "
                f"score {score}; {kinds}{signals}"
            )
        return 0
    if args.command == "match":
        return _match(args)
    if args.command == "permute":
        return _permute(parser, args)
    if args.command == "definition-placement":
        report = measure_placements(Harvest(args.harvest), args.unit, args.method,
                                    args.reference_source, args.save, args.budget)
        print(f"{report['selected']}: {len(report['verification']['worse'])} lower unit scores; {report['evaluated']} placements")
        return 0
    if args.command == "sweep":
        return _sweep(args)
    if args.command == "layout":
        return _layout(parser, args)
    if args.command == "repair":
        return _repair(args)
    if args.command == "rerun":
        return _rerun(args)
    if args.command == 'resize-forms':
        report = initialization.diagnose(Harvest(args.harvest), args.unit, args.method, args.member, args.element_type, args.restore)
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + '\n')
        for name, row in report['variants'].items():
            data = [s for s in row['sections'] if s['name'] == '.gcc_except_table']
            print(f"{name}: {row['error'] or str(row['exact']) + ' exact functions'}; exception table {[s.get('exact') for s in data]}")
        return int(any(row['error'] for row in report['variants'].values()))
    if args.command == "refresh":
        if args.header:
            found = blockers.refresh_header(Harvest(args.harvest), args.unit, args.header, set(args.methods), apply=args.apply)
        else:
            found = blockers.stubs(Harvest(args.harvest), [args.unit], apply=args.apply, only=set(args.methods), refresh=True, companion_first=args.companion_first)
        for row in found:
            print(f"{row.name}: {'refreshed' if row.applied else '; '.join(row.errors) or 'compiles'}")
        missing = set(args.methods) - {row.name for row in found}
        if missing:
            print('not found in current and ancestor source: ' + ', '.join(sorted(missing)))
        return int(bool(missing or any(row.errors for row in found)))
    if args.command == "symbols-check":
        report = symbol_audit.run(args.harvest.resolve(), args.evidence)
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        suspect = [row for row in report['findings'] if row['status'] == 'suspect']
        for row in suspect:
            print(f"suspect {row['address']:#x} {row['symbol']}: {'; '.join(row['reasons'])}")
        unverified = sum(row['status'] == 'unverified-not-emitted' for row in report['findings'])
        print(f"{len(report['findings'])} rows checked: {len(suspect)} suspect, {unverified} not emitted")
        return int(bool(suspect))
    if args.command == "symbols-merge":
        try:
            rows = symbol_table.merge(*(symbol_table.load(getattr(args, side)) for side in ('base', 'ours', 'theirs')))
            args.output.write_text(symbol_table.render(rows))
        except (OSError, ValueError) as error:
            print(f"symbols-merge: {error}")
            return 1
        print(f"merged {len(rows)} symbols -> {args.output}")
        return 0
    if args.command == "scores":
        return _scores(parser, args)
    if args.command == "vtables":
        return _vtables(args)
    if args.command == "overrides":
        return _overrides(args)
    if args.command == "frames":
        harvest = Harvest(args.harvest)
        if args.census:
            result = frames.census(harvest)
            print(f"{result['distinct_addresses']} named size- and call-identical candidates, {result['bytes']} target bytes")
        elif args.probe_constructor:
            if not all((args.unit, args.symbol, args.call)):
                parser.error('temporary probes require --unit, --symbol and --call')
            result = frames.probe_temporary(harvest, harvest.source(args.unit), args.symbol, args.call, args.argument, args.probe_constructor, args.apply)
            print(f"{result['symbol']}: chosen {result['chosen']}, applied {result['applied']}")
        else:
            if not all((args.unit, args.symbol, args.dumps)):
                parser.error('frames requires --census or --unit, --symbol and --dumps')
            result = frames.analyze(harvest, harvest.source(args.unit), args.symbol, args.dumps)
            print(f"{result['symbol']}: {len(result['partitions'])} expand partitions; {len(result['constraints'])} aligned slot constraints; size/calls match: {result['size_and_calls_match']}")
        args.save.write_text(json.dumps(result, indent=2) + "\n")
        return 0
    if args.command == "inline-budget":
        if args.apply and not args.probe_constructions:
            parser.error('--apply requires --probe-constructions')
        report = inline_budget.load(args.dump)
        if args.params_def:
            import hashlib

            metadata_path = args.dump.parent / 'metadata.json'
            metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
            report['budgets'] = inline_budget.budgets(report, inline_budget.parameters(args.params_def, metadata.get('command', [])))
            report['params_def_sha256'] = hashlib.sha256(args.params_def.read_bytes()).hexdigest()
            report['compiler'] = metadata
        if args.harvest or args.unit or args.symbol:
            if not (args.harvest and args.unit and args.symbol):
                parser.error('literal sites require --harvest, --unit and --symbol')
            harvest = Harvest(args.harvest)
            report['verification'] = inline_budget.verify_dump(harvest, args.unit, args.symbol, args.dump)
            report['literal_sites'] = inline_budget.sites(harvest, args.unit, args.symbol)
        if args.probe_constructions:
            if not (args.harvest and args.unit and len(args.symbol) == 1):
                parser.error('--probe-constructions requires --harvest, --unit and one --symbol')
            report['probe'] = inline_budget.probe(Harvest(args.harvest), args.unit, args.symbol[0], apply=args.apply)
        if args.against:
            baseline = inline_budget.load(args.against)
            report['comparison'] = inline_budget.compare(baseline, report)
            report['baseline'] = {key: baseline[key] for key in ('dump', 'sha256', 'starting_insns', 'final_insns')}
        output = json.dumps(report, indent=2) + "\n"
        if args.save:
            args.save.write_text(output)
        print(f"pass_ipa_inline: {report['starting_insns']} -> {report['final_insns']} estimated instructions; {report['inlined_calls']} calls inlined")
        for reason, count in report['rejections'].items():
            print(f"  {count}: {reason}")
        for row in report.get('comparison', {}).get('functions', [])[:12]:
            print(f"  {row['delta']:+d}: {row['name']}")
        for function in report.get('literal_sites', []):
            differences = [r for r in function['sites'] if r['ours']['decision'] != r['target']['decision']]
            print(f"  {function['symbol']}: {len(function['sites'])} literal sites, {len(differences)} different decisions")
            for row in differences:
                print(f"    {row['source_line']}: {row['literal']}: ours {row['ours']['decision']}, target {row['target']['decision']}")
        if 'probe' in report:
            probe = report['probe']
            print(f"  {len(probe['candidates'])} construction overlays; chosen: {probe['chosen'] or 'none'}; applied: {probe['applied']}")
        return 0
    if args.command == "inlines":
        harvest = Harvest(args.harvest)
        units = args.units or sorted(p.stem for p in harvest.reports.glob("*.json"))
        if args.semantic:
            units = [u for u in units if (harvest.root / 'third_party/irrlicht-0.7/source/Irrlicht' / Path(harvest.source(u)).name).exists()]
        reports = inlines.gather(harvest, units)
        if args.save:
            args.save.write_text(json.dumps(reports, indent=2) + "\n")
        for result in reports:
            if args.semantic:
                for row in result['semantic-candidates']:
                    print(f"{result['unit']}: {row['status']}: {row['symbol']} size {row['ours_size']}/{row['target_size']}, inline budget {row['inline_copy_budget']}, other calls {row['other_direct_call_delta']:+d}, indirect {row['indirect_call_delta']:+d}")
                continue
            lines = inlines.show(result)
            findings = len(lines) - 1
            print(lines[0] if findings else lines[0] + ": no differences")
            for line in lines[1:]:
                print(line)
        return 0
    if args.command == "calls-constants":
        result = calls_constants.gather(Harvest(args.harvest), args.units)
        if args.save:
            args.save.write_text(json.dumps(result, indent=2) + "\n")
        print("\n".join(calls_constants.show(result)))
        print(f"{result['compared']} inexact functions compared, {len(result['functions'])} differ, {len(result['skipped'])} skipped")
        return 0
    if args.command == "statics":
        result = statics.gather(Harvest(args.harvest), args.units)
        if args.save:
            args.save.write_text(json.dumps(result, indent=2) + "\n")
        print("\n".join(statics.show(result)))
        return 0
    if args.command == "causal-trial":
        report = census.counterfactual(Harvest(args.harvest), args.header, args.line, args.save, args.unit,
                                       authored=tuple(args.authored) if args.authored else None, variant=args.variant)
        for row in report['candidates']:
            gained = sum(len(u.get('gained_exact', [])) for u in row['units'])
            lost = sum(len(u.get('lost_exact', [])) for u in row['units'])
            worse = sum(len(u.get('worse', [])) for u in row['units'])
            print(f"{row['description']}: {gained} newly exact copies, {lost} lost, {worse} lower scores")
        return 0
    if args.command == "causal-census":
        report = census.run(Harvest(args.harvest), args.save, args.min_score, args.maximum, args.reuse)
        for row in report['constructs'][:20]:
            print(f"{row['function_count']:4} functions {row['function_bytes']:8} bytes {row['path']}:{row['line']} {row['text']}")
        print(f"{report['analyzed_functions']}/{report['selected_functions']} functions analyzed; {len(report['failures'])} failures")
        return 0
    if args.command == "causal":
        if args.apply and not args.search:
            raise ValueError('--apply requires --search')
        if args.search and args.budget < 1:
            raise ValueError('the causal search budget must be positive')
        harvest = Harvest(args.harvest)
        directory = args.dumps or causal.collect(harvest, args.unit)
        report = causal.analyze(harvest, args.unit, args.symbol, directory, limit=args.limit,
                                offsets=set(args.offset) if args.offset else None)
        if args.search:
            report['search'] = causal.search(harvest, report, budget=args.budget, apply=args.apply)
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        print('\n'.join(causal.show(report)))
        return 0
    if args.command == "destructors":
        report = destructors.diagnose(Harvest(args.harvest), args.unit, args.header, args.cls)
        if args.save:
            args.save.write_text(json.dumps(report, indent=2) + "\n")
        for name, row in report["variants"].items():
            print(f"{name}: {row['exact']} exact, {len(row['worse'])} worse" + (f"; {row['error']}" if row["error"] else ""))
            for symbol, size in row["destructor_sizes"].items():
                before = report["variants"]["baseline"]["destructor_sizes"].get(symbol)
                if size != before:
                    print(f"  {symbol}: {before} -> {size} bytes")
        return 0
    if args.command == "blockers":
        return _blockers(args)
    if args.command == "rename":
        return _rename(args)
    if args.command == "learn-calls":
        from dataclasses import asdict

        if args.object and (len(args.units) != 1 or args.constructor_aliases):
            parser.error("--object requires one unit and cannot reconcile constructor aliases")
        overrides = {Harvest(args.harvest).source(args.units[0]): args.object.resolve()} if args.object else None
        sites, learned = calls.discover(args.harvest.resolve(), args.units, overrides=overrides, windows=args.windows)
        if args.constructor_aliases:
            learned = calls.constructor_aliases(args.harvest.resolve(), sites)
        if args.save:
            hashes = calls.input_hashes(args.harvest.resolve())
            if args.object:
                import hashlib
                hashes[str(args.object.resolve())] = hashlib.sha256(args.object.read_bytes()).hexdigest()
            args.save.write_text(json.dumps({"sites": [asdict(s) for s in sites], "learned": learned, "input_hashes": hashes}, indent=2) + "\n")
        if args.apply:
            (calls.apply_aliases if args.constructor_aliases else calls.apply)(args.harvest.resolve(), learned)
        for name, (address, evidence) in sorted(learned.items()):
            print(f"{address:#x} {name}  {evidence}")
        print(f"{len(sites)} aligned call sites; {len(learned)} addresses {'saved' if args.apply else 'proven'}")
        return 0
    if args.command == "clean":
        removed, freed = Harvest(args.harvest).clean(args.older_than * 3600)
        print(f"removed {removed} work directories, {freed / 1e9:.1f} GB")
        return 0
    if args.command == "author":
        harvest = Harvest(args.harvest)
        if args.seed_manifest or args.overlay_manifest:
            manifest_path = args.seed_manifest or args.overlay_manifest
            manifest = json.loads(manifest_path.read_text())
            files = {name: (manifest_path.parent / path).read_bytes() for name, path in manifest["files"].items()}
            if args.overlay_manifest:
                report = authored.overlay(harvest, files, write=args.apply)
            else:
                report = authored.seed(harvest, files, manifest["units"], write=args.apply, claims=manifest.get("claims"))
            if args.save:
                args.save.write_text(json.dumps(report, indent=2) + "\n")
            for name, row in report.get("evaluations", {}).items():
                print(f"{name}: {row['error'] or str(len(row['exact'])) + ' exact functions'}")
            for gain in report.get("gains", []):
                print(f"{gain['symbol']}: {gain['before']} -> {gain['score']}; oracle={gain['oracle']}")
            print(f"author files: {'accepted' if report['accepted'] else 'rejected'}; applied={report['applied']}")
            return 0 if report["accepted"] else 1
        if not args.unit or not args.function:
            raise ValueError("author requires --unit and function, or --seed-manifest")
        unit = harvest.source(args.unit)
        report = authored.apply(harvest, unit, args.function, write=args.apply, variant=args.variant,
                                candidate=args.candidate.read_bytes() if args.candidate else None,
                                source_candidate=args.source_candidate.read_bytes() if args.source_candidate else None)
        patch = (
            results.save_patch(args.results, report.get("path", unit), report["original"], report["source"]) if report["applied"] else None
        )
        results.record(
            args.results,
            {
                "command": "author",
                "harvest": results.commit(harvest.root),
                "unit": unit,
                "path": report.get("path", unit),
                "symbol": args.function,
                "chosen": report["chosen"],
                "applied": report["applied"],
                "oracle": report["candidates"][report["chosen"]].get("oracle") if report["chosen"] else None,
                "patch": patch,
            },
        )
        for name, row in report["candidates"].items():
            flags = ", ".join(k for k in ("error" if row["error"] else None, "exact" if row["exact"] else None, "kept" if row["kept"] else None) if k)
            score = f"{row['before']} -> {row['score']}" if "before" in row else str(row["score"])
            print(f"  {name}: {flags or 'rejected'} (score {score})")
        if "dependents" in report:
            print(f"  header {report['path']}: checked {len(report['dependents'])} dependent units")
        print(f"author {unit} {args.function}: {report['chosen'] or 'no candidate accepted'}{' (applied)' if report['applied'] else ''}")
        return 0 if report["chosen"] else 1
    if args.command == "enum-switch":
        rows = enumswitch.run(
            Harvest(args.harvest), unit=args.unit, symbol=args.function, write=args.apply
        )
        for row in rows:
            status = "kept" if row["kept"] else "rejected"
            applied = " and applied" if row["applied"] else ""
            print(
                f"{status}{applied} {row['unit']} {row['symbol']} line {row['line']} "
                f"({row['kind']}, {row['missing_checks']} missing range checks): "
                f"{row['before_size']} -> {row['size']} bytes, "
                f"score {row['before_score']} -> {row['score']}"
            )
            if row["oracle"] is not None:
                print(f"  oracle {json.dumps(row['oracle'], sort_keys=True)}")
            if row["error"]:
                print(f"  error {row['error']}")
        if not rows:
            print("no inexact enum range checks found")
        return 0
    if args.command == "place":
        harvest = Harvest(args.harvest)
        if args.object and (len(args.units) != 1 or args.restore_literals):
            parser.error("--object needs exactly one unit and cannot restore literals")
        placed = ([place.learn_object(harvest, args.units[0], args.object)] if args.object
                  else [place.place(harvest, unit, args.restore_literals) for unit in args.units])
        if args.save:
            args.save.write_text(json.dumps(placed, indent=2) + "\n")
        for row in placed:
            print(row["output"].splitlines()[0])
            if args.object:
                print(f"  {len(row['learned'])} addresses learned from candidate; source unchanged")
            else:
                print(f"  {len(row['sections'])} called sections verified; {len(row['literals'])} literal repairs exact")
        return 0
    if args.command == "place-extent":
        result = place.place_extent(Harvest(args.harvest), args.unit, args.symbol, args.address)
        state = "already named" if result["already_named"] else "named"
        print(f"{state} {result['unit']} {result['symbol']} at {result['address']:#x}: "
              f"{result['size']} exact bytes, {result['fixed_bytes']} fixed, "
              f"{result['relocation_bytes']} relocation bytes masked")
        return 0
    if args.command == "place-bss":
        rows = place.place_bss(Harvest(args.harvest))
        if args.save:
            args.save.write_text(json.dumps(rows, indent=2) + "\n")
        for row in rows:
            print(f"{row['unit']}: .bss = {row['address']:#x}")
        print(f"{len(rows)} .bss placements learned from static initializer references")
        return 0
    if args.command == "bench":
        return _bench(args)
    if args.command == "header":
        for name in args.names:
            written = header(args.harvest.resolve(), name)
            print(f"{name}: " + (", ".join(written) if written else "nothing written (Harvest has it, or Irrlicht does not)"))
        return 0
    if args.command == "complete":
        return _complete(args)
    if args.command == "unguard":
        return _unguard(args)
    if args.command == "phase":
        return _phase(args)
    if args.command == "limits":
        return _limits(args)
    parser.error(f"unknown command {args.command}")


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
    elif args.unit and args.function:
        targets = [(harvest.source(args.unit), args.function)]
    else:
        parser.error("pass --unit and a function, or --near-miss")
    if args.skip_logged:
        settings = {k: getattr(args, k) for k in ("budget", "batch", "depth", "seed")}
        done = results.searched(results.load(args.results), "permute", settings)
        targets = [t for t in targets if t not in done]
    if args.near_miss is not None:
        print(f"{len(targets)} functions score at least {args.near_miss}")
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


def _seed(args: argparse.Namespace) -> int:
    exact = 0
    units = []
    for unit in args.units:
        try:
            result = seed(args.harvest.resolve(), unit, args.complete, frozenset(args.allow_worse), args.stub)
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"error      {unit}: {str(error).strip().splitlines()[-1]}", flush=True)
            continue
        units.append(result.unit)  # the Mac debug map's path, which can differ from the one given
        placed = " ".join(f"{k}={v:#x}" for k, v in result.placements.items())
        if result.error:
            print(f"error      {unit}: {result.error.splitlines()[0] if result.error else ''}", flush=True)
            for line in result.error.splitlines()[1:6]:
                print(f"           {line}")
        else:
            print(f"{'exact' if result.exact == result.functions else 'partial':10} {unit}  functions {result.exact}/{result.functions}  {placed}", flush=True)
            for item in result.stubbed:
                print(f"           stubbed {item}")
            if result.port_conflicts and replace_in_port(args.harvest.resolve(), result.unit):
                print(f"           the port defines {len(result.port_conflicts)} of its functions; listed as replaced in port/build.zig")
            exact += result.exact == result.functions
    seeded = [unit for unit in units if f'["{unit}"]' in (args.harvest / "config" / BUILD / "units.toml").read_text()]
    if (args.harvest / "port" / "build.zig").exists() and seeded:
        for unit, symbols in keep_port_linking(args.harvest.resolve(), seeded).items():
            print(f"port       {unit}: needs {len(symbols)} symbols nothing defines ({symbols[0]}); listed as replaced in port/build.zig")
    if len(args.units) > 1:
        print(f"{exact} of {len(args.units)} units exact")
    return 0


def _unguard(args: argparse.Namespace) -> int:
    root = args.harvest.resolve()
    path = root / "src" / args.header
    cls = args.cls or Path(args.header).stem
    header = path.read_text()
    if args.base:  # the header as committed, to choose bodies again once a unit emits the members
        shown = subprocess.run(["git", "-C", str(root), "show", f"{args.base}:src/{args.header}"], capture_output=True, text=True, check=True)
        header = shown.stdout
    includes = tuple(args.include)
    bodies = definitions((root / args.port).read_text(), cls) if args.port else {}
    taken = []
    if args.irrlicht and args.unit:
        original = (root / "third_party" / "irrlicht-0.7" / args.irrlicht).read_text(encoding="latin-1")
        irrlicht = {name: body for name, body in irrlicht_definitions(original, cls).items() if name in bodies}
        # per member, in order of preference: Irrlicht's body, then that body without its null check
        candidates = [(name, body, "Irrlicht's") for name, body in irrlicht.items()]
        candidates += [(name, bare, "Irrlicht's, unchecked") for name, body in irrlicht.items() if (bare := without_guard(body))]
        items = {"port": (args.unit, {f"src/{args.header}": unguard(header, bodies, includes).encode()})}
        for index, (name, body, _) in enumerate(candidates):
            items[f"m{index}"] = (args.unit, {f"src/{args.header}": unguard(header, {**bodies, name: body}, includes).encode()})
        evaluations = Harvest(root).evaluate_overlays(items)
        if evaluations["port"].error:
            print(evaluations["port"].error[-1500:])
            return 1
        best = dict(evaluations["port"].fuzzy)
        for index, (name, body, label) in enumerate(candidates):
            symbols = [s for s in evaluations["port"].fuzzy if _member(s) == f"{cls}::{name}"]
            swapped = evaluations[f"m{index}"]
            first = label == "Irrlicht's"  # Irrlicht's own body wins a tie with the port's; a variant must score higher
            if symbols and not swapped.error and all(
                swapped.fuzzy.get(s, 0) >= best[s] if first else swapped.fuzzy.get(s, 0) > best[s] for s in symbols
            ):
                bodies[name] = body
                best.update({s: swapped.fuzzy[s] for s in symbols})
                taken = [item for item in taken if not item.startswith(f"{name} (")] + [f"{name} ({label})"]
    path.write_text(unguard(header, bodies, includes))
    print(f"exposed {args.header}" + (f" with the members {args.port} defines" if args.port else ""))
    if taken:
        print(f"  bodies not the port's: {', '.join(taken)}")
    return 0


def _member(symbol: str) -> str:
    """Class::member of a mangled member function."""
    name = subprocess.run(["c++filt", symbol], capture_output=True, text=True, check=False).stdout.split("(")[0]
    return "::".join(name.strip().split("::")[-2:])


def _rename(args: argparse.Namespace) -> int:
    root = args.harvest.resolve()
    for unit in args.units:
        path = root / "src" / unit
        text = path.read_text()
        taken = helper_taken(root, text, unit)
        names = helper_names(text, taken)
        if not names:
            print(f"{unit}: nothing to rename")
            continue
        path.write_text(name_helpers(text, taken))
        print(f"{unit}: " + ", ".join(f"{marker} -> {names[marker]}" for marker in sorted(names)))
    return 0


def _blockers(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    units = args.units or sorted(
        path.relative_to(harvest.root / "src").as_posix()
        for path in (harvest.root / "src").rglob("*.cpp")
        if b"__builtin_trap();" in path.read_bytes()
    )
    found = blockers.stubs(harvest, units, apply=args.apply, only=set(args.only) if args.only else None)
    for stub in found:
        status = "restored" if stub.applied else "; ".join(sorted(stub.causes)) or "compiles now"
        print(f"{stub.unit}  {stub.name}: {status}", flush=True)
    print(f"\n{'cause':70} {'stubs':>6} {'only cause':>11}")
    for name, count, alone in blockers.ranked(found):
        print(f"{name[:70]:70} {count:6} {alone:11}")
    print(f"{len(found)} stubs in {len(units)} units; {sum(not s.errors for s in found)} compile with Irrlicht's body now")
    if args.apply:
        print(f"{sum(s.applied for s in found)} bodies restored; run symbol learning and whole-build scores before keeping them")
    return 0


def _bench(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    pool = bench.functions(harvest)
    sample = random.Random(args.seed).sample(pool, min(args.functions, len(pool)))
    found = bench.cases(harvest, sample, args.seed, args.per_kind)
    if args.two_edits:
        found += bench.pairs(harvest, sample, args.seed, args.two_edits)
    print(f"{len(found)} cases from {len(sample)} of {len(pool)} exact functions", flush=True)
    if args.locate:
        return _bench_locate(harvest, found, args)
    outcomes = []
    for case in found:
        outcome = bench.solve(harvest, case, args.budget, args.seed, not args.undirected)
        outcomes.append(outcome)
        if args.save:
            row = {
                "harvest": results.commit(harvest.root), "unit": case.unit, "symbol": case.symbol,
                "mutation": case.mutation, "description": case.description, "score": case.score,
                "match": outcome.match, "match_tried": outcome.match_tried,
                "permute": outcome.permute, "permute_tried": outcome.permute_tried,
                "budget": args.budget, "seed": args.seed, "directed": not args.undirected,
            }  # fmt: skip
            results.record(args.save, row)
        print(
            f"  {'match' if outcome.match else '-----'} {'permute' if outcome.permute else '-------'}  "
            f"{case.mutation:26} {case.score:6.2f}  {_member(case.symbol)}",
            flush=True,
        )
    print(f"\n{'mutation':27} {'inverse':8} {'cases':>5} {'match':>6} {'permute':>8} {'score after':>12} {'compiles':>9}")
    for name, known, count, by_match, by_permute, mean, cost in bench.table(outcomes):
        print(f"{name:27} {'yes' if known else 'no':8} {count:5} {by_match:6} {by_permute:8} {mean:12.2f} {cost:9.1f}")
    total = len(outcomes)
    either = sum(o.match or o.permute for o in outcomes)
    print(f"{'all':27} {'':8} {total:5} {sum(o.match for o in outcomes):6} {sum(o.permute for o in outcomes):8}   either: {either}")
    return 0


def _bench_locate(harvest: Harvest, found: list, args: argparse.Namespace) -> int:
    located = []
    for case in found:
        try:
            item = bench.locate(harvest, case)
        except (KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
            print(f"  error {case.mutation}: {str(error).strip().splitlines()[-1][:120]}", flush=True)
            continue
        located.append(item)
        if args.save:
            row = {
                "harvest": results.commit(harvest.root), "unit": case.unit, "symbol": case.symbol,
                "mutation": case.mutation, "description": case.description, "causes": item.causes,
                "attributed": sorted(item.attributed), "changed": sorted(item.changed), "length": item.length,
            }  # fmt: skip
            results.record(args.save, row)
        distance = "none" if item.distance is None else str(item.distance)
        print(f"  {case.mutation:26} distance {distance:>4}  lines {len(item.attributed):3}/{item.length:<4} {_member(case.symbol)}", flush=True)
    print(f"\n{'mutation':27} {'cases':>5} {'pointed':>8} {'on line':>8} {'within 2':>9} {'share of lines':>15}")
    for name, count, pointed, on_line, near, share in bench.location_table(located):
        print(f"{name:27} {count:5} {pointed:8} {on_line:8} {near:9} {share:15.2f}")
    return 0


def _fill(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    ox_type, unit = args.fill
    unit = harvest.source(unit)
    header = TYPES[ox_type][0]
    path = harvest.root / "src" / header
    original = path.read_text()
    locked = set(scan(harvest, unit).locked)
    if not locked:
        print(f"{unit} has no function locked by the declaration count")
        return 0
    for member in absent(harvest.root, ox_type):
        before = path.read_text()
        if not complete(harvest.root, {(ox_type, member)}):
            continue
        now = state(harvest, [unit])
        if not now:  # the member does not compile
            path.write_text(before)
            continue
        if all(now.get((unit, symbol), (False,))[0] for symbol in locked) and settle(
            harvest, {header: original}, frozenset(args.allow_worse)
        ):
            print(f"{unit}: {len(locked)} locked functions exact after adding members up to {ox_type}.{member}")
            return 0
    path.write_text(original)
    print(f"no run of {ox_type}'s Irrlicht members makes {unit}'s locked functions exact; {header} is unchanged")
    return 1


def _complete(args: argparse.Namespace) -> int:
    if args.fill:
        return _fill(args)
    harvest = Harvest(args.harvest)
    failed = 0
    for item in args.members:
        ox_type, member = item.split(".", 1)
        header = TYPES[ox_type][0]
        path = harvest.root / "src" / header
        before = path.read_text()
        if not complete(harvest.root, {(ox_type, member)}):
            print(f"skipped    {item}: Irrlicht's class has no such member, or {header} already has it")
            continue
        allowed = frozenset(args.allow_worse)
        settled = settle(harvest, {header: before}, allowed)
        if not settled:  # the member may need a header its Irrlicht original includes
            path.write_text(before)
            complete(harvest.root, {(ox_type, member)})
            settled = _bring_includes(harvest.root, ox_type) and settle(harvest, {header: before}, allowed)
        if settled:
            print(f"added      {item} to {header}", flush=True)
        else:
            path.write_text(before)
            failed += 1
            print(f"rejected   {item}: functions in units including {header} get worse", flush=True)
    return 1 if failed else 0


def _phase(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    units = args.units or sorted(harvest.source(path.stem) for path in harvest.reports.glob("*.json"))
    saved, locked, witnesses = {}, 0, 0
    for unit in units:
        try:
            result = scan(harvest, unit, args.phases)
        except (OSError, RuntimeError, KeyError) as error:
            print(f"error      {unit}: {str(error).strip().splitlines()[-1]}")
            continue
        saved[result.unit] = {symbol: sorted(phases) for symbol, phases in result.exact_at.items()}
        locked += len(result.locked)
        witnesses += len(result.witnesses)
        best = result.best
        print(
            f"{result.unit}: exact {result.exact(0)}/{len(result.exact_at)}, locked {len(result.locked)}, "
            f"witnesses {len(result.witnesses)}" + (f", best phase {best} with {result.exact(best)}" if best else ""),
            flush=True,
        )
        for label, group in (("locked", result.locked), ("witness", result.witnesses)):
            names = subprocess.run(["c++filt"], input="\n".join(sorted(group)), capture_output=True, text=True, check=False)
            for name, symbol in zip(names.stdout.splitlines(), sorted(group), strict=True):
                print(f"  {label:8} exact at {spans(group[symbol]):12} {name}")
        if args.save:
            args.save.write_text(json.dumps({"phases": args.phases, "units": saved}, indent=1, sort_keys=True))
    print(f"{locked} locked functions (right body, wrong declaration count) and {witnesses} witnesses in {len(saved)} units")
    return 0


def _limits(args: argparse.Namespace) -> int:
    from concord.limits import SETTINGS, scan

    harvest = Harvest(args.harvest)
    units = args.units or sorted(harvest.source(path.stem) for path in harvest.reports.glob("*.json"))
    saved, throttled = {}, 0
    for unit in units:
        try:
            result = scan(harvest, unit)
        except (OSError, RuntimeError, KeyError) as error:
            print(f"error      {unit}: {str(error).strip().splitlines()[-1]}")
            continue
        saved[result.unit] = {symbol: sorted(found) for symbol, found in result.exact_at.items()}
        throttled += len(result.throttled)
        counts = ", ".join(f"{name} {result.exact(name)}" for name in result.settings if name not in result.errors)
        print(f"{result.unit}: exact {result.exact('baseline')}/{len(result.exact_at)} baseline ({counts})", flush=True)
        names = subprocess.run(["c++filt"], input="\n".join(sorted(result.throttled)), capture_output=True, text=True, check=False)
        for name, symbol in zip(names.stdout.splitlines(), sorted(result.throttled), strict=True):
            print(f"  throttled exact at {','.join(sorted(result.throttled[symbol])):24} {name}")
    if args.save:
        args.save.write_text(json.dumps({"settings": SETTINGS, "units": saved}, indent=1, sort_keys=True))
    print(f"{throttled} functions inexact as compiled but exact under lowered limits in {len(saved)} units")
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
    if args.base and (args.against or args.ref):
        parser.error("--base cannot be combined with --against or --ref")
    if not (args.save or args.against or args.base):
        parser.error("pass --save, --against or both")
    baseline = None
    configuration_baseline = None
    if args.base:
        baseline, current, evidence = scores.normalized(args.harvest, args.base)
        configuration_baseline = evidence.get("configuration_baseline")
        current['normalization'] = evidence
    else:
        current = scores.snapshot(scores.report(args.harvest, args.capture, args.ref))
    if args.save:
        args.save.write_text(json.dumps(current, sort_keys=True))
        print(f"saved {len(current['functions'])} functions and {len(current['data'])} matched data runs to {args.save}")
    if not args.against and baseline is None:
        return 0
    before = baseline if baseline is not None else json.loads(args.against.read_text())

    def summarize(before_snapshot: dict, after_snapshot: dict, label: str = "") -> tuple[int, int]:
        changes, lost, gained = scores.compare(before_snapshot, after_snapshot)

        def show(s: dict | None) -> str:
            return "absent" if s is None else ("matched" if s["matched"] else f"{s['fuzzy']:.2f}")

        for change in changes:
            status = 'WORSE' if change.worse else 'better'
            prefix = f"{label} " if label else ""
            print(f"{prefix}{status:6} {show(change.before):>8} -> {show(change.after):8} {int(change.address):#x} {change.name}")
        worse = sum(change.worse for change in changes)
        newly = sum(1 for change in changes if change.after and change.after["matched"] and not (change.before and change.before["matched"]))
        summary = f"{len(changes)} functions changed: {newly} newly matched, {worse} worse; data bytes matched: +{gained} -{lost}"
        print(f"{label}: {summary}" if label else summary)
        return worse, lost

    source_worse, source_lost = summarize(before, current)
    if configuration_baseline is not None:
        config_worse, config_lost = summarize(configuration_baseline, before, "configuration")
    else:
        config_worse = config_lost = 0
    return 1 if source_worse or source_lost or config_worse or config_lost else 0


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


def _overrides(args: argparse.Namespace) -> int:
    harvest = Harvest(args.harvest)
    for unit in args.units:
        for line in restore_vtable_overrides(harvest, unit, apply=args.apply):
            print(f"{unit}: {line}")
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
