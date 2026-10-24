"""The emitted-copy comparison: pure findings over report-shaped inputs, and
call extraction from a real assembled object."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.inlines import Emitted, ObjectCode, analyze, read_code, show


def code(functions):
    funcs = {name: Emitted(*spec) for name, spec in functions.items()}
    return ObjectCode(funcs, {n: [] for n in funcs}, {n: 0 for n in funcs})


def report(unit="d/A.cpp", sections=(), unplaced=()):
    return {"unit": unit, "sections": list(sections), "unplaced_sections": list(unplaced)}


def section(name, address=None, functions=()):
    row = {"name": name, "size": 0, "placement": "symbol: x", "exact": True}
    if address is not None:
        row["address"] = hex(address)
    if functions:
        row["functions"] = list(functions)
    return row


def function(symbol, address, size):
    return {"symbol": symbol, "address": hex(address), "size": size, "extent": "fde", "exact": False}


ARGS = {
    "named": {},
    "extents_": {},
    "span": (0, 0),
    "mac_by_symbol": {},
    "mac_by_unit": [],
    "emitted_units": {},
    "exact_units": {},
}


def analyze_(report_, ours, target=None, **kwargs):
    return analyze(report_, ours, target or ObjectCode({}, {}, {}), **{**ARGS, **kwargs})


class Copies(unittest.TestCase):
    def test_placed_copy_matching_the_extent_reports_nothing(self):
        ours = code({"_ZW": (".text._ZW", 0, 12)})
        result = analyze_(
            report(sections=[section(".text._ZW", 0x1000, [function("_ZW", 0x1000, 12)])]),
            ours,
            extents_={0x1000: (12, "fde")},
        )
        self.assertEqual((result["differs"], result["unplaced"], result["target-only"]), ([], [], []))

    def test_a_different_size_means_a_different_definition(self):
        ours = code({"_ZW": (".text._ZW", 0, 373)})
        result = analyze_(
            report(sections=[section(".text._ZW", 0x1000, [function("_ZW", 0x1000, 373)])]),
            ours,
            extents_={0x1000: (25, "fde")},
        )
        self.assertEqual(
            result["differs"],
            [{"symbol": "_ZW", "ours": 373, "target": 25, "emitted-elsewhere": [], "exact-elsewhere": []}],
        )

    def test_elsewhere_evidence_distinguishes_context_from_definition(self):
        ours = code({"_ZW": (".text._ZW", 0, 373), "_ZX": (".text._ZX", 0, 373)})
        result = analyze_(
            report(sections=[
                section(".text._ZW", 0x1000, [function("_ZW", 0x1000, 373)]),
                section(".text._ZX", 0x2000, [function("_ZX", 0x2000, 373)]),
            ]),
            ours,
            extents_={0x1000: (25, "fde"), 0x2000: (30, "fde")},
            emitted_units={"_ZW": {"d/A.cpp", "d/B.cpp", "d/C.cpp"}, "_ZX": {"d/A.cpp", "d/B.cpp"}},
            exact_units={"_ZW": {"d/B.cpp", "d/C.cpp"}},
        )
        self.assertEqual(result["differs"][0]["emitted-elsewhere"], ["d/B.cpp", "d/C.cpp"])
        self.assertEqual(result["differs"][0]["exact-elsewhere"], ["d/B.cpp", "d/C.cpp"])
        self.assertEqual(result["differs"][1]["emitted-elsewhere"], ["d/B.cpp"])
        self.assertEqual(result["differs"][1]["exact-elsewhere"], [])

    def test_text_functions_compare_the_same_way(self):
        ours = code({"_ZF": (".text", 0, 671)})
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 671)])]),
            ours,
            extents_={0x1000: (345, "fde")},
        )
        self.assertEqual(result["differs"][0]["symbol"], "_ZF")

    def test_unplaced_copy_verdicts(self):
        ours = code({
            "_Zexpected": (".text._Zexpected", 0, 10),
            "_Zelsewhere": (".text._Zelsewhere", 0, 20),
            "_Zhome": (".text._Zhome", 0, 30),
            "_Zsurplus": (".text._Zsurplus", 0, 40),
        })
        result = analyze_(
            report(
                unplaced=[f".text.{n}" for n in ours.functions],
                sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 1)])],
            ),
            ours,
            extents_={0x1100: (30, "fde")},
            span=(0x1000, 0x2000),
            mac_by_symbol={"_Zexpected": {"d/A.cpp"}, "_Zelsewhere": {"d/B.cpp"}},
        )
        self.assertEqual(
            {r["symbol"]: r["verdict"] for r in result["unplaced"]},
            {"_Zexpected": "expected", "_Zelsewhere": "elsewhere", "_Zhome": "possible-home", "_Zsurplus": "surplus"},
        )
        self.assertEqual(result["unplaced"][2]["extents"], ["0x1100"])

    def test_an_unclaimed_extent_is_something_the_original_emitted(self):
        ours = code({"_ZF": (".text", 0, 10)})
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10)])]),
            ours,
            named={0x1100: "_Zknown"},
            extents_={0x1100: (50, "fde"), 0x1200: (60, "fde")},
            span=(0x1000, 0x2000),
        )
        self.assertEqual(
            result["target-only"],
            [
                {"address": 0x1100, "size": 50, "symbol": "_Zknown", "kind": "fde", "called": 0},
                {"address": 0x1200, "size": 60, "symbol": None, "kind": "fde", "called": 0},
            ],
        )

    def test_an_extent_the_unit_calls_counts_even_outside_the_span(self):
        ours = code({"_ZF": (".text", 0, 10)})
        target = code({"_ZF": (".text", 0, 10), "_ZG": (".text", 0x40, 10)})
        target.direct["_ZG"] = ["sub_800"]
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10)])]),
            ours,
            target,
            extents_={0x800: (42, "fde"), 0x900: (50, "fde")},
            span=(0x1000, 0x2000),
            named={0x900: "_Znamed"},
        )
        self.assertEqual(
            result["target-only"],
            [{"address": 0x800, "size": 42, "symbol": None, "kind": "fde", "called": 1}],
        )

    def test_an_extent_hosting_an_unplaced_copy_is_not_missing(self):
        ours = code({"_Zcopy": (".text._Zcopy", 0, 50)})
        result = analyze_(
            report(
                unplaced=[".text._Zcopy"],
                sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10)])],
            ),
            ours,
            extents_={0x1100: (50, "fde")},
            span=(0x1000, 0x2000),
        )
        self.assertEqual(result["target-only"], [])

    def test_mac_rows_we_do_not_emit(self):
        ours = code({"_ZF": (".text", 0, 10)})
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10)])]),
            ours,
            mac_by_unit=[
                {"symbol": "_Zkept", "marker": "d/A.h"},
                {"symbol": "_ZF", "marker": "d/A.cpp"},
                {"symbol": "_ZmacInternal", "marker": "vector"},
            ],
            emitted_units={"_Zkept": {"d/B.cpp"}},
        )
        self.assertEqual(result["mac-missing"], [{"symbol": "_Zkept", "marker": "d/A.h"}])
        self.assertEqual(result["mac-platform"], 1)

    def test_static_initializer_matches_by_kind_not_name(self):
        ours = code({"_GLOBAL__I__ZF": (".text", 0, 10)})
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_GLOBAL__I__ZF", 0x1000, 10)])]),
            ours,
            mac_by_unit=[{"symbol": "_GLOBAL__I_a", "marker": "d/A.cpp"}],
        )
        self.assertEqual(result["mac-missing"], [])


class Calls(unittest.TestCase):
    def test_direct_callees_and_indirect_counts(self):
        ours = code({"_ZF": (".text", 0, 10)})
        ours.direct["_ZF"] = ["_Zcallee", "_Zcallee", "_Zother"]
        ours.indirect["_ZF"] = 3
        target = code({"_ZF": (".text", 0, 10)})
        target.direct["_ZF"] = ["_Zcallee", "_Ztarget"]
        [row] = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10)])]),
            ours,
            target,
        )["call-differences"]
        self.assertEqual(row["called-out-of-line"], {"_Zcallee": 1, "_Zother": 1})
        self.assertEqual(row["original-called"], {"_Ztarget": 1})
        self.assertEqual(row["indirect"], {"ours": 3, "target": 0})

    def test_callees_compare_by_target_address(self):
        ours = code({"_ZF": (".text", 0, 10)})
        ours.direct["_ZF"] = ["_Zplaced", "_Zunknown"]
        target = code({"_ZF": (".text", 0, 10)})
        target.direct["_ZF"] = ["sub_2000", "sub_3000"]
        [row] = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10), function("_Zplaced", 0x2000, 5)])]),
            ours,
            target,
            named={0x3000: "_Zlost"},
        )["call-differences"]
        self.assertEqual(row, {"function": "_ZF", "called-out-of-line": {"_Zunknown": 1}, "original-called": {"_Zlost": 1}})

    def test_same_named_callee_cancels_before_addresses(self):
        ours = code({"_ZF": (".text", 0, 10)})
        ours.direct["_ZF"] = ["_Zmoved"]
        target = code({"_ZF": (".text", 0, 10)})
        target.direct["_ZF"] = ["_Zmoved"]
        result = analyze_(
            report(sections=[section(".text", 0x1000, [function("_ZF", 0x1000, 10), function("_Zmoved", 0x2000, 5)])]),
            ours,
            target,
            named={0x9000: "_Zmoved"},
        )
        self.assertEqual(result["call-differences"], [])


@unittest.skipUnless(shutil.which("as"), "GNU assembler is needed")
class ReadCode(unittest.TestCase):
    assembly = """.text
.globl _ZF
.type _ZF,@function
_ZF:
call _Zcallee
jmp _Ztail
call *0x8(%rax)
lea .LC0(%rip),%rdi
.size _ZF,.-_ZF
.section .text._ZW,"axG",@progbits,_ZW,comdat
.weak _ZW
.type _ZW,@function
_ZW:
ret
.size _ZW,.-_ZW
.section .rodata
.LC0: .string "x"
"""

    def test_sections_calls_and_indirect(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "a.o"
            (Path(tmp) / "a.s").write_text(self.assembly)
            subprocess.run(["as", Path(tmp) / "a.s", "-o", obj], check=True)
            code_ = read_code(obj)
        self.assertEqual(code_.functions["_ZF"].section, ".text")
        self.assertEqual(code_.functions["_ZW"].section, ".text._ZW")
        self.assertEqual(sorted(code_.direct["_ZF"]), ["_Zcallee", "_Ztail"])
        self.assertEqual(code_.indirect["_ZF"], 1)


class Show(unittest.TestCase):
    def test_renders_each_finding_kind(self):
        result = {
            "unit": "d/A.cpp",
            "emitted": 2,
            "placed": 1,
            "differs": [{"symbol": "_ZD", "ours": 10, "target": 5, "emitted-elsewhere": ["d/B.cpp"], "exact-elsewhere": ["d/B.cpp"]}],
            "unplaced": [{"symbol": "_ZS", "size": 9, "verdict": "surplus", "mac_units": [], "extents": [], "emitted-elsewhere": [], "exact-elsewhere": []}],
            "target-only": [{"address": 0x1100, "size": 3, "symbol": "_ZK", "kind": "fde", "called": 2}],
            "mac-missing": [{"symbol": "_ZM", "marker": "d/A.h"}],
            "mac-platform": 4,
            "call-differences": [{"function": "_ZF", "called-out-of-line": {"_ZC": 2}, "indirect": {"ours": 1, "target": 0}}],
        }
        text = "\n".join(show(result))
        for fragment in ("_ZD", "_ZS", "0x1100", "_ZK", "d/A.h", "_ZM", "_ZC x2", "indirect calls: ours 1"):
            self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
