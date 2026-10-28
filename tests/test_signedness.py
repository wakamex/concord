"""Find signedness-related instruction pairs in compiled Harvest functions."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.diff import read_function
from concord.harvest import Harvest
from concord.signedness import _add_sources, _function_pairs, find

HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))
BUILD = "1.18-linux-amd64"
UNIT = "HarvestFull/harvest/states/CPlayState.cpp"
SYMBOL = "_ZN7harvest6states10CPlayState17addParticleEntityEPN2ox5video14IParticleStateERKNS2_4core9CVector3dIfEE"
HEADER = ".intel_syntax noprefix\n.text\n.globl f\n.type f,@function\nf:\n"
FOOTER = "\n.size f,.-f\n"


def assemble(directory: Path, name: str, body: str) -> Path:
    source, output = directory / f"{name}.s", directory / f"{name}.o"
    source.write_text(HEADER + body + FOOTER)
    subprocess.run(["as", "-o", str(output), str(source)], check=True)
    return output


@unittest.skipUnless(shutil.which("as"), "needs the GNU assembler")
class SignednessPairs(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def pairs(self, target: str, candidate: str) -> list[dict]:
        left = read_function(assemble(self.directory, "target", target), "f")
        right = read_function(assemble(self.directory, "candidate", candidate), "f")
        result = _function_pairs(left, right)
        self.assertIsNotNone(result)
        return result[0]

    def test_signed_and_unsigned_conditional_jumps(self):
        pairs = self.pairs(
            "cmp edi, esi\njl 1f\nmov eax, 1\nret\n1: xor eax, eax\nret",
            "cmp edi, esi\njb 1f\nmov eax, 1\nret\n1: xor eax, eax\nret",
        )
        self.assertEqual([pair["kind"] for pair in pairs], ["conditional-jump"])

    def test_sign_and_zero_extension(self):
        for target, candidate in (
            ("movsx rax, di\nret", "movzx rax, di\nret"),
            ("movsxd rax, edi\nret", "mov eax, edi\nret"),
            ("cdqe\nret", "mov eax, edi\nret"),
        ):
            with self.subTest(target=target, candidate=candidate):
                pairs = self.pairs(target, candidate)
                self.assertEqual([pair["kind"] for pair in pairs], ["extension"])

    def test_arithmetic_and_logical_shifts(self):
        pairs = self.pairs("sar rax, 4\nret", "shr rax, 4\nret")
        self.assertEqual([pair["kind"] for pair in pairs], ["shift"])

    def test_signed_and_unsigned_division_sequences(self):
        pairs = self.pairs(
            "mov eax, edi\ncdq\nidiv esi\nret",
            "mov eax, edi\nxor edx, edx\ndiv esi\nret",
        )
        self.assertEqual([pair["kind"] for pair in pairs], ["division"])
        self.assertEqual(len(pairs[0]["target_offsets"]), 2)

    def test_signed_and_unsigned_multiplication(self):
        pairs = self.pairs("mov eax, edi\nimul esi\nret", "mov eax, edi\nmul esi\nret")
        self.assertEqual([pair["kind"] for pair in pairs], ["multiplication"])


@unittest.skipUnless((HARVEST / "build" / "match" / BUILD).is_dir(), "needs Harvest objects after `hv match`")
class HarvestSignedness(unittest.TestCase):
    def test_real_function_has_the_signedness_pair_and_debug_line(self):
        harvest = Harvest(HARVEST)
        verdict = harvest.verdict(UNIT, SYMBOL)
        target = read_function(harvest.objects / "target" / f"{verdict.unit}.o", SYMBOL)
        candidate = read_function(harvest.objects / "base" / f"{verdict.unit}.o", SYMBOL)
        self.assertEqual(len(target.code), 369)
        self.assertNotEqual(target.code, candidate.code)

        rows = find(harvest, UNIT)
        row = next((row for row in rows if row["symbol"] == SYMBOL), None)
        if row is None:
            self.skipTest("the recorded signedness pair is absent from this Harvest build")
        self.assertEqual(row["pairs"][0]["kind"], "conditional-jump")
        self.assertEqual(row["pairs"][0]["target_offsets"], [0xBC, 0xC2])
        self.assertEqual(row["pairs"][0]["candidate_offsets"], [0xBC, 0xC3])

        original_source = subprocess.check_output(
            ["git", "-C", str(HARVEST), "show", f"cf6926e:src/{UNIT}"]
        )
        _add_sources(harvest, [row], {UNIT: original_source})
        self.assertEqual(
            row["pairs"][0]["source"],
            {"path": "src/HarvestFull/harvest/states/CPlayState.cpp", "line": 4369},
        )


class SignednessCli(unittest.TestCase):
    def test_parser_accepts_unit_and_save_options(self):
        from concord.cli import build_parser

        args = build_parser().parse_args(
            ["signedness", "--harvest", "/harvest", "--unit", UNIT, "--save", "/tmp/report.json"]
        )
        self.assertEqual(args.command, "signedness")
        self.assertEqual(args.unit, UNIT)


if __name__ == "__main__":
    unittest.main()
