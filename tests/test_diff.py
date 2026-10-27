"""The diff's cause attribution, on objects assembled from known code and on
Harvest's documented inexact functions."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from concord.diff import diff_code, read_function
from concord.model import Cause

HEADER = ".intel_syntax noprefix\n.text\n.globl f\n.type f,@function\nf:\n"
FOOTER = "\n.size f,.-f\n"


def assemble(directory: Path, name: str, body: str) -> Path:
    source, output = directory / f"{name}.s", directory / f"{name}.o"
    source.write_text(HEADER + body + FOOTER)
    subprocess.run(["as", "-o", str(output), str(source)], check=True)
    return output


@unittest.skipUnless(shutil.which("as"), "needs the GNU assembler")
class AssembledCauses(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)

    def causes(self, target: str, candidate: str) -> set[Cause]:
        a = read_function(assemble(self.directory, "target", target), "f")
        b = read_function(assemble(self.directory, "candidate", candidate), "f")
        result = diff_code(a, b)
        self.assertFalse(result.exact)
        return {f.cause for f in result.findings}

    def test_identical_code_is_exact(self):
        body = "mov eax, [rdi + 8]\nret"
        a = read_function(assemble(self.directory, "a", body), "f")
        b = read_function(assemble(self.directory, "b", body), "f")
        self.assertTrue(diff_code(a, b).exact)

    def test_register_allocation(self):
        self.assertEqual(
            self.causes(
                "mov rcx, [rdi]\nadd rcx, rsi\nmov rax, rcx\nret",
                "mov rdx, [rdi]\nadd rdx, rsi\nmov rax, rdx\nret",
            ),
            {Cause.REGISTER_ALLOCATION},
        )

    def test_swapped_compare_with_mirrored_jump_is_operand_order(self):
        self.assertEqual(
            self.causes(
                "cmp edi, esi\njl 1f\nmov eax, 1\nret\n1: xor eax, eax\nret",
                "cmp esi, edi\njg 1f\nmov eax, 1\nret\n1: xor eax, eax\nret",
            ),
            {Cause.OPERAND_ORDER},
        )

    def test_swapped_blocks_are_block_order(self):
        self.assertEqual(
            self.causes(
                "test edi, edi\nje 1f\nmov eax, 7\nadd eax, esi\nret\n1: mov eax, 3\nsub eax, esi\nret",
                "test edi, edi\njne 1f\nmov eax, 3\nsub eax, esi\nret\n1: mov eax, 7\nadd eax, esi\nret",
            ),
            {Cause.BLOCK_ORDER},
        )

    def test_member_offset_is_struct_layout(self):
        self.assertEqual(
            self.causes("mov eax, [rdi + 8]\nret", "mov eax, [rdi + 12]\nret"),
            {Cause.STRUCT_LAYOUT},
        )

    def test_stack_slot_is_stack_layout(self):
        self.assertEqual(
            self.causes("mov [rsp + 8], edi\nret", "mov [rsp + 16], edi\nret"),
            {Cause.STACK_LAYOUT},
        )

    def test_call_against_inline_body_is_inlining(self):
        self.assertEqual(
            self.causes(
                "push rbx\ncall g\npop rbx\nret",
                "push rbx\nlea eax, [rdi + rdi*2]\nimul eax, esi\nshl eax, 3\nadd eax, 7\npop rbx\nret",
            ),
            {Cause.INLINING},
        )

    def test_an_unaligned_call_can_also_come_from_a_shared_tail(self):
        target = read_function(assemble(self.directory, "target", (
            "test edi, edi\nje 1f\nadd esi, 1\n1: call g\nret"
        )), "f")
        candidate = read_function(assemble(self.directory, "candidate", (
            "test edi, edi\nje 1f\nadd esi, 1\ncall g\nret\n1: call g\nret"
        )), "f")
        findings = [f.detail for f in diff_code(target, candidate).findings if f.cause == Cause.INLINING]
        self.assertTrue(findings)
        self.assertIn("shared", " ".join(findings))
        self.assertNotIn("has inline code", " ".join(findings))

    def test_extra_spill_is_register_allocation(self):
        self.assertEqual(
            self.causes(
                "mov eax, edi\nadd eax, esi\nret",
                "mov [rsp - 8], esi\nmov eax, edi\nadd eax, [rsp - 8]\nret",
            )
            - {Cause.INSTRUCTION_SELECTION},
            {Cause.REGISTER_ALLOCATION},
        )


HARVEST = Path(os.environ.get("CONCORD_HARVEST", "../harvest"))


@unittest.skipUnless((HARVEST / "build" / "match").is_dir(), "needs a Harvest checkout after `hv match`")
class HarvestDocumentedCauses(unittest.TestCase):
    """Functions whose remaining difference Harvest's docs/matching.md records."""

    CASES: ClassVar = [
        (
            "HarvestFull/harvest/entity/CPerimeterBomb.cpp",
            "_ZN7harvest6entity23CPerimeterBombExplosion11updateLogicEf",
            Cause.OPERAND_ORDER,
        ),
        (
            "HarvestFull/harvest/entity/CSparkMoverEntity.cpp",
            "_ZN7harvest6entity17CSparkMoverEntityC1Eff",
            Cause.OPERAND_ORDER,
        ),
        (
            "HarvestFull/harvest/gui/CProfileScreen.cpp",
            "_ZN7harvest3gui14CProfileScreen11saveProfileEb",
            Cause.BLOCK_ORDER,
        ),
        (
            "HarvestFull/harvest/game/CThreatLevel.cpp",
            "_ZN7harvest4game12CThreatLevel19alienOccursOnPlanetEii",
            Cause.BLOCK_ORDER,
        ),
        (
            "HarvestFull/harvest/settings/CProfileManager.cpp",
            "_ZN7harvest8settings15CProfileManager17createProfileListEv",
            Cause.REGISTER_ALLOCATION,
        ),
    ]

    def test_documented_cause_is_the_main_finding(self):
        from concord.harvest import Harvest

        harvest = Harvest(HARVEST)
        for unit, symbol, cause in self.CASES:
            with self.subTest(symbol=symbol):
                try:
                    verdict = harvest.verdict(unit, symbol)
                except KeyError:
                    self.skipTest(f"{symbol} is no longer in the report")
                if verdict.row["exact"]:
                    self.skipTest(f"{symbol} is exact now")
                findings = harvest.diff(verdict).findings
                self.assertTrue(findings)
                self.assertTrue(all(f.cause == cause for f in findings), [f.cause.value for f in findings])



class KnowledgeBase(unittest.TestCase):
    def test_every_compiler_file_loads_with_known_causes(self):
        from concord import knowledge

        self.assertIn("gcc-4.4.3", knowledge.compilers())
        for compiler in knowledge.compilers():
            idioms = knowledge.load(compiler)
            self.assertTrue(idioms)
            self.assertEqual(len({i.id for i in idioms}), len(idioms))
            for i in idioms:
                self.assertIn(i.status, ("confirmed", "hypothesis"))
                self.assertTrue(i.fix and i.symptom)
                self.assertTrue(all("commit" in e or "note" in e for e in i.evidence), i.id)

    def test_findmesh_cleanup_thunk_diagnosis_is_recorded(self):
        from concord import knowledge

        entries = [i for i in knowledge.load("gcc-4.4.3") if i.id == "findmesh-cleanup-thunk"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.status, "hypothesis")
        self.assertIn("cleanup thunk", entry.symptom)
        note = entry.evidence[0]["note"]
        self.assertIn("0x4c2c20", note)
        self.assertIn("0x4c7b60", note)
        self.assertIn("606", note)
        self.assertIn("522", note)

    def test_the_reader_size_diagnosis_is_recorded(self):
        from concord import knowledge

        entries = [
            e for i in knowledge.load("gcc-4.4.3")
            for e in i.evidence
            if e.get("function") == "CXFileReader MeshNormals and AnimationSet restored sizes"
        ]
        self.assertEqual(len(entries), 1)
        note = entries[0]["note"]
        self.assertIn("fast_atof_move", note)
        self.assertIn("operator=", note)

    def test_xml_reader_wide_conversion_gate_is_recorded(self):
        from concord import knowledge

        entries = [i for i in knowledge.load("gcc-4.4.3") if i.id == "carried-include-context"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.status, "confirmed")
        self.assertIn("wcstombs", entry.symptom)
        self.assertIn("0x534050", entry.limits)
        self.assertIn("setText+0x9e", entry.limits)
        self.assertIn("0x6d to 0x5d", entry.limits)
        self.assertIn("95.89651", entry.limits)
        notes = "\n".join(e["note"] for e in entry.evidence)
        self.assertIn("2,897", notes)
        self.assertIn("7 functions changed: 3 newly matched, 2 worse; data bytes matched: +0 -0", notes)
        self.assertIn("5 functions changed: 5 newly matched, 0 worse; data bytes matched: +0 -0", notes)
        self.assertIn("99.977165", notes)
        self.assertIn("0 candidate differences from the original", notes)

    def test_cg_parameter_namespace_diagnosis_is_available_for_immediates(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.IMMEDIATE_OR_RELOC})
            if i.id == "cg-program-namespace"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIn("CG_PROGRAM = 4109", entry.fix)
        self.assertIn("CG_LINE", entry.symptom)
        self.assertIn("99.48623", entry.limits)
        self.assertEqual(len(entry.evidence), 2)

    def test_misplaced_image_constructor_is_available_for_reference_diagnosis(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.IMMEDIATE_OR_RELOC})
            if i.id == "misplaced-image-constructor-reference"
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].status, "confirmed")
        note = entries[0].evidence[0]["note"]
        self.assertIn("0x4decb0", note)
        self.assertIn("0x4def32", note)
        self.assertIn("0x60e660", note)

    def test_opengl_vertex_pointer_bases_resolve_to_the_same_fields(self):
        from concord import knowledge

        entries = [i for i in knowledge.load("gcc-4.4.3") if i.id == "equivalent-member-pointer-bases"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.cause, Cause.STRUCT_LAYOUT)
        self.assertEqual(entry.status, "confirmed")
        self.assertIn("0x3020 + 0x14", entry.fix)
        self.assertIn("0x3030 + 0x04", entry.fix)
        self.assertIn("0 bases differ", entry.evidence[0]["note"])

    def test_vector_insert_aux_double_width_is_cursor_positioning(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.IMMEDIATE_OR_RELOC})
            if i.id == "vector-insert-aux-cursor-position"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.status, "confirmed")
        self.assertIn("two element widths", entry.fix)
        notes = "\n".join(e["note"] for e in entry.evidence)
        self.assertIn("0x500989", notes)
        self.assertIn("0x500a50", notes)
        self.assertIn("0x59919f", notes)
        self.assertIn("0x599200", notes)
        self.assertIn("CGUIEnvironment", notes)
        self.assertIn("0x64", notes)


if __name__ == "__main__":
    unittest.main()
