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

    def test_adjacent_register_moves_in_the_opposite_order_are_instruction_order(self):
        self.assertEqual(
            self.causes(
                "mov r13, r9\nmov rbx, rax\nret",
                "mov rbx, rax\nmov r13, r9\nret",
            ),
            {Cause.INSTRUCTION_ORDER},
        )

    def test_alignment_padding_encodings_are_padding(self):
        self.assertEqual(
            self.causes("nop\nret", "xchg ax, ax\nret"),
            {Cause.PADDING},
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
                substantive = [f.cause for f in findings if f.cause != Cause.PADDING]
                self.assertTrue(all(found == cause for found in substantive), [f.cause.value for f in findings])



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

    def test_new_game_random_sum_diagnosis_is_recorded(self):
        from concord import knowledge

        entries = {i.id: i for i in knowledge.load("gcc-4.4.3")}
        entry = entries["new-game-random-sum"]
        self.assertEqual(entry.status, "hypothesis")
        self.assertEqual(entry.cause.value, "instruction-selection")
        notes = [e["note"] for e in entry.evidence if e.get("function")]
        self.assertEqual(len(notes), 1)
        record = entry.limits + " " + notes[0]
        for fact in (
            "0x4ae210",
            "99.77819",
            "132 legal positions",
            "expand",
            "0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0",
        ):
            self.assertIn(fact, record)

    def test_operand_order_noop_trials_are_recorded(self):
        from concord import knowledge

        entry = next(i for i in knowledge.load("gcc-4.4.3") if i.id == "compare-operand-order")
        notes = {e["function"]: e["note"] for e in entry.evidence if "function" in e and "note" in e}
        expected = {
            "daisy::gui::CGUIButton::OnEvent": ("oldState != State", "99.92453"),
            "ox::core::CAes::expandKey": ("k + 4 > j", "99.90991"),
            "harvest::game::harvest_getSelectedBuilding": ("buildings->end() == it", "99.652176"),
        }
        for function, facts in expected.items():
            with self.subTest(function=function):
                self.assertIn(function, notes)
                for fact in facts:
                    self.assertIn(fact, notes[function])
                self.assertIn("identical", notes[function])

    def test_zip_sort_helper_diagnosis_is_recorded(self):
        from concord import knowledge

        entries = {i.id: i for i in knowledge.load("gcc-4.4.3")}
        comparison_notes = {
            e.get("function"): e.get("note", "")
            for e in entries["compare-operand-order"].evidence
            if e.get("function") and e.get("note")
        }
        self.assertIn("daisy::io::CZipReader sort helpers", comparison_notes)
        for fact in ("0x53cac0", "0x53df00", "no `operator>`"):
            self.assertIn(fact, comparison_notes["daisy::io::CZipReader sort helpers"])

        order_notes = {
            e.get("function"): e.get("note", "")
            for e in entries["definition-order"].evidence
            if e.get("function") and e.get("note")
        }
        self.assertIn("daisy::io::CZipReader sort helpers", order_notes)
        for fact in (
            "phase reports 24/30 exact, 0 locked and 0 witnesses",
            "101 actual scratch searches and 0 failed resets",
            "128 orders, 2 restarts and seed 0 plateaued with 0 exact gains",
        ):
            self.assertIn(fact, order_notes["daisy::io::CZipReader sort helpers"])

    def test_copy_loop_advice_records_ssa_recycling_and_the_unresolved_source(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.OPERAND_ORDER})
            if i.id == "inliner-pass-operand-order"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIn("SSA", entry.fix)
        self.assertIn("source correction remains unidentified", entry.fix)
        notes = [e["note"] for e in entry.evidence if e.get("function") == "CStringFunctions::millisecondsToWide"]
        self.assertEqual(len(notes), 1)
        self.assertIn("len_929", notes[0])
        self.assertIn("31", notes[0])
        self.assertIn("0x5e0e48", notes[0])

    def test_opengl_source_causes_and_bounded_blockers_are_recorded(self):
        from concord import knowledge

        entries = {i.id: i for i in knowledge.load("gcc-4.4.3")}
        for key in ("opengl-quad-index-and-bound", "opengl-live-viewport-and-version", "opengl-unit-context-blockers"):
            self.assertIn(key, entries.keys())
            self.assertTrue(entries[key].evidence)
        blockers = entries["opengl-unit-context-blockers"]
        self.assertIn("queryFeature", blockers.limits)
        self.assertIn("setBasicRenderStates", blockers.limits)
        self.assertIn("_M_insert_aux", blockers.limits)

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

    def test_filesystem_alias_operand_order_attempts_are_recorded(self):
        from concord import knowledge

        entries = [
            evidence
            for idiom in knowledge.load("gcc-4.4.3")
            for evidence in idiom.evidence
            if evidence.get("function") == "daisy::io::CFileSystem::intern_resolveAliases"
        ]
        self.assertEqual(len(entries), 1)
        note = entries[0]["note"]
        for detail in (
            "0x7ad",
            "cmp al, cl",
            "split-start-initializer",
            "split-end-initializer",
            "99.985794",
        ):
            with self.subTest(detail=detail):
                self.assertIn(detail, note)

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
        self.assertEqual(len(entry.evidence), 3)
        self.assertIn("12 call rows", entry.evidence[-1]["note"])
        self.assertIn("nor a fresh calls-constants run finds a direct-callee difference for printVariables", entry.evidence[-1]["note"])

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

    def test_opengl_clipped_vertex_pointer_trials_are_available_to_layout_diagnosis(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.STRUCT_LAYOUT})
            if i.id == "equivalent-member-pointer-bases"
        ]
        self.assertEqual(len(entries), 1)
        trials = [e for e in entries[0].evidence if e.get("function") == "CVideoOpenGL clipped vertex-pointer trials"]
        self.assertEqual(len(trials), 1)
        note = trials[0]["note"]
        self.assertIn("99.74632 to 74.8125", note)
        self.assertIn("99.80786 to 75.41485", note)
        self.assertIn("same vertex store order and registers", note)
        self.assertIn("+0xbb and +0xe2", note)
        self.assertIn("byte-identical object", note)
        self.assertIn("source form responsible remains unknown", entries[0].limits)

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

    def test_octree_render_block_order_trials_are_recorded(self):
        from concord import knowledge

        entries = [i for i in knowledge.load("gcc-4.4.3") if i.id == "octree-render-local-init-order"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.cause, Cause.BLOCK_ORDER)
        self.assertEqual(entry.status, "hypothesis")
        self.assertIn("two-intervention limit", entry.limits)
        note = entry.evidence[0]["note"]
        for detail in ("99.73%", "99.69%", "99.71%", "+0x14e5", "+0x1835", "phase scan"):
            with self.subTest(detail=detail):
                self.assertIn(detail, note)

    def test_shadow_vertex_switch_limit_is_recorded(self):
        from concord import knowledge

        entries = [i for i in knowledge.load("gcc-4.4.3") if i.id == "switch-or-if-chain"]
        self.assertEqual(len(entries), 1)
        notes = [
            e["note"] for e in entries[0].evidence
            if e.get("function") == "daisy::scene::CShadowVolumeSceneNode::setMeshToRenderFrom at 0x57a010"
        ]
        self.assertEqual(len(notes), 1)
        for detail in (
            "+0x01fa", "+0x0200", "+0x1070", "4393 bytes", "4377 bytes",
            "92.0941", "0 locked functions", "0 witnesses", "four successful searches",
            "85 c0", "83 f8 01", "0f 85 6a 0e 00 00", "0f 84 69 0e 00 00",
            "0f 1f 00", "66 90", "0f 1f 84 00 00 00 00 00", "neither form improved",
        ):
            with self.subTest(detail=detail):
                self.assertIn(detail, notes[0])

    def test_animation_player_register_allocation_trials_are_recorded(self):
        from concord import knowledge

        entries = [
            i for i in knowledge.load("gcc-4.4.3")
            if i.id == "animation-player-register-copy-order"
        ]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.cause, Cause.REGISTER_ALLOCATION)
        self.assertEqual(entry.status, "hypothesis")
        self.assertIn("0x583e60", entry.evidence[0]["function"])
        self.assertIn("36 sibling placements", entry.limits)
        self.assertIn("two-intervention limit", entry.limits)
        note = entry.evidence[0]["note"]
        for detail in (
            "4d 89 cd 48 89 c3", "48 89 c3 4d 89 cd", "99.962616",
            "lastTime", "0 functions changed: 0 newly matched, 0 worse",
        ):
            with self.subTest(detail=detail):
                self.assertIn(detail, note)


if __name__ == "__main__":
    unittest.main()
