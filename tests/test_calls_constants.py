"""The call-and-constant sweep: cancellation and pairing logic on constructed
references, and extraction from real compiled objects."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.calls_constants import (
    Callee,
    Constant,
    DataRef,
    FunctionRefs,
    _canonical,
    compare_function,
    read_refs,
    resolvers,
)


def refs(callees=(), constants=(), datarefs=(), indirect=0, code=b"\x90"):
    return FunctionRefs(
        len(code), code,
        tuple(Callee(*c) for c in callees),
        tuple(Constant(*c) for c in constants),
        tuple(DataRef(*c) for c in datarefs),
        indirect,
    )


def constant(value, mnemonic="mov", site="byte ptr [rsp + 0x2f]", operands=(("mem", "rsp", None, 1, 0x2F, 1),), offset=0):
    return (offset, value, (mnemonic, operands), site, f"{mnemonic} {site}, {value}")


_RESOLVE_OURS, _RESOLVE_TARGET, _DISPLAY = resolvers({}, {}, {})
ARGS = {"resolve_ours": _RESOLVE_OURS, "resolve_target": _RESOLVE_TARGET, "display": _DISPLAY}


def compare(ours, target, **kwargs):
    return compare_function(ours, target, **{**ARGS, **kwargs})


class Calls(unittest.TestCase):
    def test_identical_callees_report_nothing(self):
        ours = refs(callees=[(4, "_Zresize"), (9, "_Zother")])
        target = refs(callees=[(4, "_Zresize"), (9, "_Zother")])
        self.assertEqual(compare(ours, target), [])

    def test_a_callee_extra_on_both_sides_is_a_substitution(self):
        ours = refs(callees=[(4, "_Zresize")])
        target = refs(callees=[(4, "sub_4f7f00")])
        [row] = compare(ours, target)
        self.assertEqual(row["classification"], "different-callees")
        self.assertEqual(row["ours"], [{"symbol": "_Zresize", "name": "_Zresize", "address": None}])
        self.assertEqual(row["target"], [{"symbol": "sub_4f7f00", "name": "0x4f7f00 sub_4f7f00", "address": "0x4f7f00"}])

    def test_one_sided_call_is_inline_copy_noise(self):
        ours = refs(callees=[(4, "_Zonly")])
        [row] = compare(ours, refs())
        self.assertEqual(row["classification"], "called-out-of-line")
        [row] = compare(refs(), ours)
        self.assertEqual(row["classification"], "original-called")

    def test_ctor_against_new_is_inline_copy_noise(self):
        ctor = "_ZN2ox4core7CStringIcEC1IcEEPKT_"
        new_array = "_Znam"
        caller_ours = refs(callees=[(4, ctor)])
        caller_target = refs(callees=[(8, new_array)])
        ctor_body = refs(callees=[(4, new_array)])
        catalog = {ctor: ctor_body}
        rows = compare(caller_ours, caller_target, ours_catalog=catalog, target_catalog=catalog)
        kinds = {row["classification"] for row in rows}
        self.assertNotIn("different-callees", kinds)
        self.assertIn("called-out-of-line", kinds)
        self.assertIn("original-called", kinds)

    def test_resize_against_reserve_stays_a_substitution(self):
        ours = refs(callees=[(4, "_Zresize")])
        target = refs(callees=[(4, "sub_4f7f00")])
        reserve_body = refs(callees=[(4, "_Zother")])
        fill_body = refs(callees=[(4, "_Znwm")])
        rows = compare(
            ours,
            target,
            ours_catalog={"_Zresize": fill_body, "sub_4f7f00": reserve_body},
            target_catalog={"_Zresize": fill_body, "sub_4f7f00": reserve_body},
        )
        self.assertEqual([row["classification"] for row in rows], ["different-callees"])

    def test_callees_cancel_by_resolved_address(self):
        ours = refs(callees=[(4, "_Zplaced")])
        target = refs(callees=[(4, "sub_2000")])
        ours_addr = {"_Zplaced": 0x2000}
        self.assertEqual(compare(ours, target, resolve_ours=ours_addr.get), [])

    def test_moved_callee_still_cancels_by_name(self):
        pair = refs(callees=[(4, "_Zmoved")])
        self.assertEqual(compare(pair, pair), [])

    def test_indirect_call_counts_report(self):
        [row] = compare(refs(indirect=2), refs(indirect=5))
        self.assertEqual(row, {"kind": "indirect", "ours": 2, "target": 5})


class Constants(unittest.TestCase):
    def test_identical_immediates_report_nothing(self):
        pair = refs(constants=[constant(1), constant(0, offset=4)])
        self.assertEqual(compare(pair, pair), [])

    def test_same_site_different_value_pairs(self):
        ours = refs(constants=[constant(1)])
        target = refs(constants=[constant(0)])
        [row] = compare(ours, target)
        self.assertEqual(row["classification"], "different-constants")
        self.assertEqual((row["ours"]["value"], row["target"]["value"]), (1, 0))
        self.assertEqual(row["ours"]["instruction"], "mov byte ptr [rsp + 0x2f], 1")

    def test_a_value_only_one_side_has_is_named_with_its_instruction(self):
        ours = refs(constants=[constant(1), constant(7, offset=8)])
        target = refs(constants=[constant(1)])
        [row] = compare(ours, target)
        self.assertEqual(row["classification"], "ours-only")
        self.assertEqual(row["ours"]["value"], 7)

    def test_counts_matter(self):
        ours = refs(constants=[constant(1), constant(1, offset=8)])
        target = refs(constants=[constant(1), constant(1, offset=8), constant(1, offset=12)])
        [row] = compare(ours, target)
        self.assertEqual(row["classification"], "target-only")


class DataReferences(unittest.TestCase):
    def test_identical_references_report_nothing(self):
        pair = refs(datarefs=[(4, ".rodata.cst4", 8, "movss", "movss xmm3, dword ptr [rip]")])
        self.assertEqual(compare(pair, pair), [])

    def test_same_base_different_offset_pairs(self):
        ours = refs(datarefs=[(4, ".rodata.cst4", 8, "movss", "movss xmm3, dword ptr [rip]")])
        target = refs(datarefs=[(4, ".rodata.cst4", 0, "movss", "movss xmm3, dword ptr [rip]")])
        [row] = compare(ours, target)
        self.assertEqual(row["classification"], "different-references")
        self.assertEqual((row["ours"]["ref"], row["target"]["ref"]), (".rodata.cst4+0x8", ".rodata.cst4+0x0"))

    def test_a_reference_only_one_side_makes(self):
        ours = refs(datarefs=[(4, "_ZTVX", 16, "mov", "mov qword ptr [rdi], 0")])
        [row] = compare(ours, refs())
        self.assertEqual(row["classification"], "ours-only")
        self.assertEqual(row["ours"]["ref"], "_ZTVX+0x10")


class CanonicalNames(unittest.TestCase):
    def test_comdat_sections_are_their_symbol(self):
        self.assertEqual(_canonical(".rodata._ZTVX"), "_ZTVX")
        self.assertEqual(_canonical(".text._ZF"), "_ZF")
        self.assertEqual(_canonical(".rodata.cst4"), ".rodata.cst4")
        self.assertEqual(_canonical(".rodata.str1.8"), ".rodata.str1.8")


class Encode2TableReferences(unittest.TestCase):
    """GCC may relocate a jump table through .rodata+N while the target names the
    object symbol at offset zero; both must compare the same."""

    @unittest.skipUnless(
        Path("/code/harvest-cursor/build/match/1.18-linux-amd64/ox__algo__CBase64url.o").exists()
        or os.environ.get("CONCORD_HARVEST"),
        "needs a Harvest checkout after hv match",
    )
    def test_base64url_encode2_agrees_on_base64alphabet2(self):
        from concord.calls_constants import compare_function, load_symbols, resolvers

        root = Path(os.environ.get("CONCORD_HARVEST", "/code/harvest-cursor"))
        build = "1.18-linux-amd64"
        sym = "_ZN2ox4algo10CBase64url7encode2ERNS_4core7CStringIcEEPNS_2io9IReadFileE"
        slug = "ox__algo__CBase64url"
        ours = read_refs(root / "build" / "match" / build / f"{slug}.o", {sym})[sym]
        target = read_refs(root / "build" / "objdiff" / build / "target" / f"{slug}.o", {sym})[sym]
        report = json.loads((root / "build" / "match" / build / f"{slug}.json").read_text())
        rows = {f["symbol"]: f for s in report["sections"] for f in s.get("functions", [])}
        named, name_to_addr = load_symbols(root, build)
        resolve_ours, resolve_target, display = resolvers(rows, named, name_to_addr)
        self.assertEqual(
            compare_function(ours, target, resolve_ours=resolve_ours, resolve_target=resolve_target, display=display),
            [],
        )


class Resolvers(unittest.TestCase):
    def test_target_sub_names_carry_their_address(self):
        _, resolve_target, _ = resolvers({}, {}, {})
        self.assertEqual(resolve_target("sub_4f7f00"), 0x4F7F00)
        self.assertIsNone(resolve_target("_Znamed"))

    def test_ours_resolves_learned_then_placed(self):
        rows = {"_Zplaced": {"address": "0x5000"}, "_Zx": {}}
        resolve_ours, _, _ = resolvers(rows, {}, {"_Zknown": 0x4000})
        self.assertEqual(resolve_ours("_Zknown"), 0x4000)
        self.assertEqual(resolve_ours("_Zplaced"), 0x5000)
        self.assertIsNone(resolve_ours("_Znone"))

    def test_display_names_resolved_addresses(self):
        _, _, display = resolvers({}, {0x4000: "_Znamed"}, {})
        self.assertEqual(display("target", "sub_4000", 0x4000), "0x4000 _Znamed")
        self.assertEqual(display("ours", "_Zunknown", None), "_Zunknown")


@unittest.skipUnless(shutil.which("g++"), "a C++ compiler is needed")
class RealObjects(unittest.TestCase):
    """Real compiled objects: a call, an immediate and a global reference must
    be extracted and compared."""

    SOURCE = """
extern "C" {
int other_a(int);
int other_b(int);
int global_flag;
}
extern "C" int f(int *p) {
    global_flag = %d;
    return %s(*p) + %d;
}
"""

    def compile_(self, flag, callee, add):
        path = self.tmp / f"c{flag}{callee}{add}.o"
        source = path.with_suffix(".cpp")
        source.write_text(self.SOURCE % (flag, callee, add))
        subprocess.run(["g++", "-O2", "-c", source, "-o", path], check=True)
        return read_refs(path)

    def setUp(self):
        self.tmpctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpctx.name)

    def tearDown(self):
        self.tmpctx.cleanup()

    def test_calls_constants_and_references(self):
        base = self.compile_(0, "other_a", 42)
        self.assertIn("f", base)
        ours = self.compile_(1, "other_b", 43)["f"]
        target = base["f"]
        rows = compare(ours, target)
        by_kind = {}
        for row in rows:
            by_kind.setdefault((row["kind"], row["classification"]), []).append(row)
        calls = by_kind.get(("call", "different-callees"), [])
        self.assertTrue(calls, f"no callee substitution in {json.dumps(rows)}")
        ours_names = {e["symbol"] for e in calls[0]["ours"]}
        target_names = {e["symbol"] for e in calls[0]["target"]}
        self.assertNotEqual(ours_names, target_names)
        self.assertTrue(all("other" in n for n in ours_names | target_names))
        self.assertTrue(
            any({r["ours"]["value"], r["target"]["value"]} == {1, 0} for r in by_kind.get(("constant", "different-constants"), []))
            or by_kind.get(("constant", "ours-only")) or by_kind.get(("constant", "target-only")),
            f"no constant difference in {json.dumps(rows)}",
        )

    def test_identical_objects_report_nothing(self):
        base = self.compile_(0, "other_a", 42)["f"]
        self.assertEqual(compare(base, base), [])


if __name__ == "__main__":
    unittest.main()
