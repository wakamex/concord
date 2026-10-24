"""Compare a unit's out-of-line inline copies with the target's.

GCC emits an out-of-line copy of each ODR-used inline function, implicit
constructor or destructor, and template instantiation as a weak symbol in a
COMDAT `.text._Z...` section. Which copies a unit emits, and how big each is,
is fixed by the definitions the unit's headers show it, so a difference from
the target's set means a header shows the unit a definition the original did
not have, or hides one it had.

Three sides are compared: the compiled object (`build/match/<build>/<unit>.o`),
the target (`build/objdiff/<build>/target/<unit>.o` delinked beside ours, the
function extents in the unit's address span, and the symbols naming them), and
the Mac debug map (`reference/1.18-mac-i386/functions.csv`), which credits
every emitted function to the unit and header that defined it.

Call sites say the same thing one level down: a direct call in ours where the
target's copy has none is a function the original inlined that we call out of
line, and vice versa.
"""

from __future__ import annotations

import csv
import json
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import capstone
from capstone import x86
from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

from concord.harvest import Harvest

MAC = "1.18-mac-i386"
CALL, TAIL_JMP = 0xE8, 0xE9  # a 5-byte direct call or tail jump's field starts at +1


@dataclass(frozen=True)
class Emitted:
    """One function in a relocatable object: its section, offset and size."""

    section: str
    offset: int
    size: int


@dataclass(frozen=True)
class ObjectCode:
    """A relocatable object's functions and their direct and indirect calls."""

    functions: dict[str, Emitted]
    direct: dict[str, list[str]]   # callee names per function
    indirect: dict[str, int]       # `call *disp(reg)` sites per function


def read_code(path: Path, calls: bool = True) -> ObjectCode:
    """Read every function symbol in an object; with `calls`, its call sites too.

    A direct callee is a PC32/PLT32 relocation (addend -4) right after a call
    or unconditional-jump opcode; other PC32 fields are rip-relative data
    reads. Indirect (virtual) calls carry no relocation and are only counted.
    """
    with path.open("rb") as stream:
        elf = ELFFile(stream)
        sections = list(elf.iter_sections())
        names = {i: s.name for i, s in enumerate(sections)}
        rels = defaultdict(list)
        for section in sections:
            if not isinstance(section, RelocationSection):
                continue
            table = sections[section["sh_link"]]
            for r in section.iter_relocations():
                if r["r_info_type"] in (2, 4) and r["r_addend"] == -4:
                    rels[section["sh_info"]].append((r["r_offset"], table.get_symbol(r["r_info_sym"]).name))
        functions, direct, indirect = {}, {}, {}
        md = None
        if calls:
            md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
            md.detail = True
        for sym in elf.get_section_by_name(".symtab").iter_symbols():
            if sym["st_info"]["type"] != "STT_FUNC" or not isinstance(sym["st_shndx"], int):
                continue
            index, start, size = sym["st_shndx"], sym["st_value"], sym["st_size"]
            functions[sym.name] = Emitted(names[index], start, size)
            if not calls:
                continue
            data = sections[index].data()
            direct[sym.name] = [
                name
                for offset, name in rels[index]
                if start <= offset < start + size and offset > 0 and data[offset - 1] in (CALL, TAIL_JMP) and name
            ]
            indirect[sym.name] = sum(
                1
                for i in md.disasm(data[start : start + size], 0)
                if i.mnemonic == "call" and i.operands and i.operands[0].type == x86.X86_OP_MEM
            )
    return ObjectCode(functions, direct, indirect)


def mac_rows(root: Path) -> tuple[dict[str, set[str]], dict[str, list[dict]]]:
    """symbol -> units the Mac debug map credits it to; unit -> its rows."""
    by_symbol, by_unit = defaultdict(set), defaultdict(list)
    path = root / "reference" / MAC / "functions.csv"
    if not path.exists():
        return by_symbol, by_unit
    for row in csv.DictReader(path.open(newline="")):
        by_symbol[row["symbol"]].add(row["unit"])
        by_unit[row["unit"]].append(row)
    return by_symbol, by_unit


def extents(root: Path, build: str) -> dict[int, tuple[int, str]]:
    """The target's function extents: address -> (size, 'fde' or 'thunk')."""
    script = (
        "from hv import extents\n"
        f"t = extents.load_target('{build}')\n"
        "for a, (s, k) in sorted(t.function_extents().items()): print(a, s, k)\n"
    )
    done = subprocess.run(["uv", "--no-config", "run", "--locked", "python", "-c", script],
                          cwd=root, capture_output=True, text=True, check=True)  # fmt: skip
    return {int(a): (int(s), k) for a, s, k in (line.split() for line in done.stdout.splitlines())}


def spans(harvest: Harvest) -> dict[str, tuple[int, int]]:
    """Each unit's target span: its `.text` placement to the next unit's `.text`.

    The linker lays each object's `.text` before its `.text.*` copies, so the
    extents between are the copies the original's unit emitted.
    """
    placed = sorted(
        (int(s["address"], 16), path.stem)
        for path in harvest.reports.glob("*.json")
        for s in json.loads(path.read_text())["sections"]
        if s["name"] == ".text" and "address" in s
    )
    return {unit: (a, placed[i + 1][0] if i + 1 < len(placed) else 1 << 62) for i, (a, unit) in enumerate(placed)}


def analyze(
    report: dict,
    ours: ObjectCode,
    target: ObjectCode,
    *,
    named: dict[int, str],
    extents_: dict[int, tuple[int, str]],
    span: tuple[int, int],
    mac_by_symbol: dict[str, set[str]],
    mac_by_unit: list[dict],
    emitted_units: dict[str, set[str]],
    exact_units: dict[str, set[str]],
) -> dict:
    """One unit's emitted-copy comparison, as structured findings.

    - `differs`: both sides emit a copy, of different sizes; the unit sees a
      different definition. `emitted-elsewhere` and `exact-elsewhere` say
      whether the same symbol is emitted, and matched, in other units: a copy
      that is exact elsewhere has a working definition and its difference is
      this unit's context.
    - `unplaced`: copies we emit that the matcher could not place, by verdict:
      `expected` (the Mac map credits this unit), `elsewhere` (it credits only
      other units), `possible-home` (an unnamed extent of the same size sits in
      the unit's span, or the name is claimed), `surplus` (no trace anywhere:
      the original likely had no out-of-line copy), with the same elsewhere
      evidence.
    - `target-only`: extents in the unit's span no function of ours was placed
      at: copies the original emitted that we have none of, including a
      possible home only when no unplaced copy of ours shares the size.
      Unnamed extents outside the span join the list when the unit's own
      target code calls them; `called` is how many of those calls exist.
    - `mac-missing`: Mac rows for the unit we emit nothing for, restricted to
      names some unit in the build does emit (the rest are the Mac headers'
      own internals and counted as `mac-platform`).
    - `call-differences`: per function on both sides, direct callees only we
      have (`called-out-of-line`: the original inlined them) or only the target
      has (`original-called`: we inlined them), and indirect-call counts.
      Callees compare by target address where one is known, so our call to a
      placed symbol and the target's to its `sub_` name are not a difference.
    """
    unit = report["unit"]
    placed_sections = {s["name"] for s in report["sections"] if "address" in s}
    unplaced_sections = set(report.get("unplaced_sections", []))
    rows = {f["symbol"]: f for s in report["sections"] for f in s.get("functions", [])}
    claimed = {int(f["address"], 16) for f in rows.values()}

    def elsewhere(symbol: str) -> dict:
        emitters = sorted(set(emitted_units.get(symbol, ())) - {unit})
        return {
            "emitted-elsewhere": emitters,
            "exact-elsewhere": sorted(set(exact_units.get(symbol, ())) - {unit}),
        }

    name_to_addr = {sym: a for a, sym in named.items()}
    placed_addr = {name: int(row["address"], 16) for name, row in rows.items()}

    def ours_addr(callee: str) -> int | None:
        return name_to_addr.get(callee) or placed_addr.get(callee)

    def target_addr(callee: str) -> int | None:
        if callee.startswith("sub_"):
            try:
                return int(callee[4:], 16)
            except ValueError:
                pass
        return name_to_addr.get(callee)

    called = Counter(
        a for callees in target.direct.values() for c in callees if (a := target_addr(c)) is not None
    )
    copies = {n: e for n, e in ours.functions.items() if e.section.startswith(".text._Z")}
    homes = defaultdict(list)
    for a, (size, kind) in sorted(extents_.items()):
        if a in claimed or not (span[0] <= a < span[1] or (called[a] and a not in named)):
            continue
        homes[size].append({"address": a, "size": size, "symbol": named.get(a), "kind": kind, "called": called[a]})

    unplaced = []
    taken = set()
    for name, e in sorted(copies.items()):
        if e.section not in unplaced_sections:
            continue
        candidates = homes.get(e.size, [])
        credit = sorted(mac_by_symbol.get(name, ()))
        unplaced.append({
            "symbol": name,
            "size": e.size,
            "verdict": (
                "expected" if unit in credit
                else "elsewhere" if credit
                else "possible-home" if candidates
                else "surplus"
            ),
            "mac_units": [u for u in credit if u != unit],
            "extents": [f"{r['address']:#x}" for r in candidates],
            **elsewhere(name),
        })
        taken.update(r["address"] for r in candidates)
    target_only = [row for rows_ in homes.values() for row in rows_ if row["address"] not in taken]

    differs = []
    for name, row in sorted(rows.items()):
        size = extents_.get(int(row["address"], 16), (row["size"],))[0]
        if size != row["size"]:
            differs.append({"symbol": name, "ours": row["size"], "target": size, **elsewhere(name)})

    has_init = any(n.startswith("_GLOBAL__I") for n in ours.functions)
    mac_missing, platform = [], 0
    for row in mac_by_unit:
        symbol = row["symbol"]
        if symbol.startswith("_GLOBAL__I"):
            if has_init:
                continue
        elif symbol in ours.functions:
            continue
        if symbol in emitted_units:
            mac_missing.append({"symbol": symbol, "marker": row["marker"]})
        else:
            platform += 1

    def call_difference(ours_calls: list[str], target_calls: list[str]) -> tuple[dict, dict]:
        """Callees only one side makes. Names cancel first; the rest cancel
        where both resolve to the same target address, so a call to a placed
        symbol matches the target's call to its `sub_` name."""
        ours_rest = [(c, ours_addr(c)) for c in (Counter(ours_calls) - Counter(target_calls)).elements()]
        they_rest = [(c, target_addr(c)) for c in (Counter(target_calls) - Counter(ours_calls)).elements()]
        avail = Counter(a for _, a in they_rest if a is not None)
        ours_only = Counter()
        for callee, a in ours_rest:
            if a is not None and avail[a]:
                avail[a] -= 1
            else:
                ours_only[callee] += 1
        avail = Counter(a for _, a in ours_rest if a is not None)
        target_only_ = Counter()
        for callee, a in they_rest:
            if a is not None and avail[a]:
                avail[a] -= 1
            else:
                target_only_[(named.get(a) or f"sub_{a:x}") if a is not None else callee] += 1
        return dict(ours_only), dict(target_only_)

    call_differences = []
    for name in sorted(set(ours.functions) & set(target.functions)):
        ours_only, target_only_ = call_difference(ours.direct.get(name, []), target.direct.get(name, []))
        entry = {}
        if ours_only:
            entry["called-out-of-line"] = ours_only
        if target_only_:
            entry["original-called"] = target_only_
        if ours.indirect.get(name, 0) != target.indirect.get(name, 0):
            entry["indirect"] = {"ours": ours.indirect[name], "target": target.indirect[name]}
        if entry:
            call_differences.append({"function": name, **entry})

    return {
        "unit": unit,
        "emitted": len(copies),
        "placed": sum(1 for e in copies.values() if e.section in placed_sections),
        "differs": differs,
        "unplaced": unplaced,
        "target-only": target_only,
        "mac-missing": mac_missing,
        "mac-platform": platform,
        "call-differences": call_differences,
    }


def gather(harvest: Harvest, units: Iterable[str]) -> list[dict]:
    """Run the comparison for each unit (source paths or report slugs)."""
    root, build = harvest.root, harvest.build
    by_symbol, by_unit = mac_rows(root)
    named = {}
    symbols_tsv = root / "config" / build / "symbols.tsv"
    if symbols_tsv.exists():
        named = {int(row["address"], 16): row["symbol"] for row in csv.DictReader(symbols_tsv.open(newline=""), delimiter="\t")}
    extents_ = extents(root, build)
    unit_spans = spans(harvest)
    emitted_units, exact_units = defaultdict(set), defaultdict(set)
    for path in harvest.reports.glob("*.json"):
        rep = json.loads(path.read_text())
        for s in rep["sections"]:
            for f in s.get("functions", []):
                emitted_units[f["symbol"]].add(rep["unit"])
                if f.get("exact"):
                    exact_units[f["symbol"]].add(rep["unit"])
        for s in rep.get("unplaced_sections", []):
            if s.startswith(".text."):
                emitted_units[s[6:]].add(rep["unit"])
    reports = []
    for unit in units:
        report = json.loads((harvest.reports / f"{harvest.slug(unit)}.json").read_text())
        slug = harvest.slug(report["unit"])
        ours = read_code(harvest.reports / f"{slug}.o")
        target_path = harvest.objects / "target" / f"{slug}.o"
        target = read_code(target_path) if target_path.exists() else ObjectCode({}, {}, {})
        reports.append(analyze(
            report,
            ours,
            target,
            named=named,
            extents_=extents_,
            span=unit_spans.get(slug, (0, 0)),
            mac_by_symbol=by_symbol,
            mac_by_unit=by_unit.get(report["unit"], []),
            emitted_units=emitted_units,
            exact_units=exact_units,
        ))
    return reports


def elsewhere_note(row: dict) -> str:
    """What other units prove about a copy: a working definition, or none."""
    if not row["emitted-elsewhere"]:
        return ""
    exact = row["exact-elsewhere"]
    if not exact:
        return "; emitted but exact in no unit"
    note = f"; the same copy is exact in {len(exact)} other {'unit' if len(exact) == 1 else 'units'}"
    return note + (f" ({', '.join(exact)})" if len(exact) <= 3 else "")


def show(result: dict) -> list[str]:
    """Render one unit's findings for the terminal."""
    out = [f"{result['unit']}: {result['emitted']} emitted copies, {result['placed']} placed, {len(result['unplaced'])} unplaced"]
    if result["mac-platform"]:
        out[0] += f"; {result['mac-platform']} Mac-map rows match nothing the build emits"
    VERDICT = {
        "expected": "unplaced; the Mac map credits this unit",
        "elsewhere": "unplaced; the Mac map credits only other units",
        "possible-home": "unplaced; a same-size unnamed extent sits in the span",
        "surplus": "we emit it; no trace of an out-of-line copy anywhere",
    }
    for row in result["differs"]:
        out.append(
            f"  different definition: {row['symbol']}  ours {row['ours']} bytes, target {row['target']}{elsewhere_note(row)}"
        )
    for row in result["unplaced"]:
        detail = VERDICT[row["verdict"]]
        if row["mac_units"]:
            detail += f" ({', '.join(row['mac_units'])})"
        out.append(f"  {detail}: {row['symbol']}  {row['size']} bytes{elsewhere_note(row)}")
    for row in result["target-only"]:
        name = row["symbol"] or "unnamed"
        called = f"; the unit's functions call it x{row['called']}" if row["called"] else ""
        out.append(f"  the target holds at {row['address']:#x}, we emit no copy ({row['size']} bytes{called}): {name}")
    for row in result["mac-missing"]:
        out.append(f"  the Mac map credits {row['marker']}, we emit no copy: {row['symbol']}")
    for row in result["call-differences"]:
        parts = [
            *(f"we call out of line {name} x{n}" for name, n in sorted(row.get("called-out-of-line", {}).items())),
            *(f"the original calls {name} x{n}" for name, n in sorted(row.get("original-called", {}).items())),
        ]
        if "indirect" in row:
            parts.append(f"indirect calls: ours {row['indirect']['ours']}, the original {row['indirect']['target']}")
        out.append(f"  {row['function']}: " + "; ".join(parts))
    return out
