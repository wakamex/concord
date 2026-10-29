import tomllib
import unittest
from pathlib import Path

from concord import knowledge
from concord.model import Cause


class EventDispatchEvidenceTests(unittest.TestCase):
    def test_block_order_guidance_includes_measured_event_dispatches(self):
        idioms = knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.BLOCK_ORDER})
        idiom = next(item for item in idioms if item.id == "event-dispatch-audit")
        notes = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertIn("compare", idiom.fix.lower())
        for function, facts in {
            "daisy::gui::CGUIWindow::OnEvent": ("62.919464", "29.791946", "same outer"),
            "harvest::states::CIntroState::OnEvent": ("67.644806", "same outer"),
            "harvest::gui::CStoryScreen::OnEvent": ("97.92593", "42.851852", "43.74074"),
        }.items():
            with self.subTest(function=function):
                for fact in facts:
                    self.assertIn(fact, notes[function])

    def test_thumb_call_evidence_keeps_unit_regressions_visible(self):
        idiom = next(item for item in knowledge.load("gcc-4.4.3") if item.id == "event-dispatch-audit")
        note = next(item["note"] for item in idiom.evidence if item["function"] == "daisy::gui::CGUIScrollBar::OnEvent")
        for fact in ("82.203125", "86.67969", "87.46484", "80.71875", "62.880596", "81.41122"):
            self.assertIn(fact, note)

    def test_large_handler_tables_are_compared_entry_by_entry(self):
        idiom = next(item for item in knowledge.load("gcc-4.4.3") if item.id == "event-dispatch-audit")
        notes = {item["function"]: item["note"] for item in idiom.evidence}
        for function, address in (
            ("harvest::states::CMainMenuState::OnEvent", "0x608150"),
            ("harvest::states::CPlayState::OnEvent", "0x608d88"),
        ):
            with self.subTest(function=function):
                self.assertIn(address, notes[function])
                self.assertIn("all eight entries", notes[function])
        menu = notes["harvest::states::CMainMenuState::OnEvent"]
        self.assertIn("five case orders", menu)
        self.assertIn("52.809746", menu)


class BspTreeSplitPolyEvidenceTests(unittest.TestCase):
    def test_splitpoly_residual_records_data_audit_and_kept_overlay(self):
        idiom = next(
            item for item in knowledge.load("gcc-4.4.3")
            if item.id == "bsp-tree-splitpoly-residual"
        )
        notes = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertEqual(idiom.status, "confirmed")
        self.assertIn("21 data rows", idiom.symptom)
        for function, facts in {
            "Initial residual": ("3,527 target bytes", "0xbd8", "0xbc8", "303 register-choice rows"),
            "Vertex local": ("94.51469 to 94.7765", "5,000-case oracle agrees", "50.79 to 50.92"),
            "Intersection comparison": ("94.7765 to 99.9106", "5,000-case oracle agrees"),
            "Index count": ("0x38e38e39", "0xaaaaaaab", "99.91571 to 99.9106", "99.91571 to 100.0"),
            "Final unit audit": (
                "1 functions changed: 1 newly matched, 0 worse; data bytes matched: +0 -0",
                "232 passed and 3 skipped",
                "findSplitter",
                "polyExistsInNode",
            ),
        }.items():
            with self.subTest(function=function):
                for fact in facts:
                    self.assertIn(fact, notes[function])


class InlinedStringReferenceKnowledgeTests(unittest.TestCase):
    def test_dropship_call_distribution_evidence_is_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        knowledge = tomllib.loads(path.read_text())
        idiom = next(item for item in knowledge["idiom"] if item["id"] == "inlined-string-reference-counts")
        evidence = next(item for item in idiom["evidence"] if item.get("commit") == "9a39d4d")

        self.assertIn("all 77 sprite loads", evidence["note"])
        self.assertIn("out-of-line CString<char>::CString(const char*)", evidence["note"])
        self.assertIn("All 52 method placements", evidence["note"])


class PlayStateOnEventCallEvidenceTests(unittest.TestCase):
    def test_current_call_audit_distinguishes_cleanup_from_event_calls(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        knowledge = tomllib.loads(path.read_text())
        idiom = next(item for item in knowledge["idiom"] if item["id"] == "ipa-inliner-distribution")
        evidence = next(
            item for item in idiom["evidence"]
            if item.get("function") == "Harvest e86091f CPlayState::OnEvent call audit"
        )

        for finding in (
            "13,487 target bytes and 13,325 candidate bytes at score 66.92",
            "six extra target calls to CString<wchar_t>::~CString()",
            "one extra target call to _Unwind_Resume",
            "no different or missing gameplay callee count",
            "807 call rows",
            "exception cleanup",
            "Two earlier int-cast overlays",
        ):
            self.assertIn(finding, evidence["note"])

    def test_latest_screen_records_rejected_candidates_and_gate(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "event-dispatch-audit")
        evidence = next(
            (
                item["note"] for item in idiom["evidence"]
                if item.get("function") == "harvest::states::CPlayState::OnEvent at Harvest a3b9f79"
            ),
            None,
        )
        self.assertIsNotNone(evidence)

        for finding in (
            "7 call rows, 12 enum-check rows, 3 data rows and 1,130 block-order rows",
            "six target-only wide CString destructor calls",
            "eight missing range checks at lines 1673 and 1678",
            "Two authored marker-field-order candidates",
            "rejected before oracle evaluation",
            "0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0",
        ):
            self.assertIn(finding, evidence)


class LuaManagerUnitEvidenceTests(unittest.TestCase):
    def test_unit_audit_separates_inlining_from_source_corrections(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        knowledge = tomllib.loads(path.read_text())
        idiom = next(item for item in knowledge["idiom"] if item["id"] == "lua-manager-unit-recovery")
        evidence = {item["function"]: item["note"] for item in idiom["evidence"]}

        self.assertIn("28,021 to 36,437", evidence["Unit context"])
        self.assertIn("same-layout", evidence["Unit context"])
        self.assertIn("push_back", evidence["CLuaManager::addModsFromFiles"])
        self.assertIn("operator=", evidence["CLuaManager::addModsFromFiles"])
        self.assertIn("before adding one", evidence["CLuaManager::hookWaveButton"])
        self.assertIn("124", evidence["CLuaManager::hookWaveButton"])


class MainMenuUnitEvidenceTests(unittest.TestCase):
    def test_unit_audit_keeps_unresolved_causes_and_rejected_trials_visible(self):
        idioms = knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.BLOCK_ORDER})
        idiom = next(item for item in idioms if item.id == "main-menu-unit-context")
        evidence = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertEqual(idiom.status, "hypothesis")
        self.assertIn("97 aligned", evidence["Unit context"])
        self.assertIn("31 declaration-count phases", evidence["Unit context"])
        self.assertIn("59.48062", evidence["CMainMenuState::secondInit"])
        self.assertIn("combined condition", evidence["CMainMenuState::secondInit"])
        self.assertIn("52.809746", evidence["CMainMenuState::OnEvent"])
        self.assertIn("59 overlays", evidence["CMainMenuState::enterGameModeSelectMode"])
        self.assertIn("recycled", evidence["CMainMenuState::enterGameModeSelectMode"])
        self.assertIn("1 newly matched", evidence["Whole-build gate"])


class PlayStateUnitEvidenceTests(unittest.TestCase):
    def test_render_guidance_distinguishes_context_trials_from_source_fixes(self):
        idioms = knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.BLOCK_ORDER})
        idiom = next(item for item in idioms if item.id == "play-state-render-unit")
        evidence = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertIn("31 declaration-count phases", evidence["Unit context"])
        self.assertIn("22 same-layout", evidence["Unit context"])
        self.assertIn("25 placed", evidence["Unit context"])
        self.assertIn("mouseY", evidence["CPlayState::render"])
        self.assertIn("5,000", evidence["CPlayState::render"])
        self.assertIn("spacing", evidence["CPlayState::renderWaveButton"])
        self.assertIn("getPosition", evidence["CPlayState::renderRangeCircleForEntity"])
        minimap = evidence["CPlayState::renderMinimap follow-up"]
        for fact in (
            "35e7c39",
            "0xffff2020",
            "unchanged at 88.927734",
            "lowers objdiff to 88.86471",
            "returned reference",
            "without an oracle",
            "target [rsp+0x70..0x7c]",
            "candidate uses [rsp+0xe0..0xec] for rightEdge",
            "and [rsp+0x60..0x6c] for marker boxes",
            "target [rsp+0x90..0x9c]",
            "[rsp+0x80..0x8c] for minimap area",
            "88.927734 to 87.59664",
            "88.927734 to 87.68823",
            "273 aligned stack-slot constraints",
            "remaining 48 bytes",
        ):
            self.assertIn(fact, minimap)
        self.assertIn("unchanged-source", evidence["CPlayState::renderRangeCircle"])
        self.assertIn("5,000", evidence["CPlayState::performRectangleSelection"])
        self.assertIn("0 worse", evidence["Whole-build gate"])
        self.assertIn("unresolved", idiom.limits)
        previous = next(
            item["note"] for group in knowledge.load("gcc-4.4.3") for item in group.evidence
            if item.get("function") == "harvest::states::CPlayState::render()"
            and item.get("commit") == "9dff9df"
        )
        self.assertIn("play-state-render-unit", previous)
        self.assertNotIn("no source line is missing or different", previous)


class MissileTurretUnitResidualTests(unittest.TestCase):
    def test_authored_changes_and_remaining_unit_blockers_are_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "harvest-missile-turret-unit-residuals")
        notes = {item["function"]: item["note"] for item in idiom["evidence"]}

        self.assertEqual(idiom["status"], "hypothesis")
        self.assertIn("73/77 functions exact", notes["Final unit snapshot"])
        self.assertIn("3 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0", notes["Final unit snapshot"])
        self.assertIn("5,000 oracle cases", notes["Accepted source corrections"])
        self.assertIn("mov qword ptr [rsp + 0x58], 0", notes["CMissileTurretEntity::findPriorityAlien"])
        self.assertIn("32 explicitly unit-scoped rewrite candidates", notes["CMissileTurretEntity::render"])
        self.assertIn("all 30 placed", notes["Unit context recheck"])
        self.assertIn("CAlienPriorities.h", notes["Header order and Mac map"])
        self.assertIn("TargetPosition.X before the BurstTime reset", notes["CMissileTurretEntity::updateLogic accepted reorder"])
        self.assertIn("97.24306 to 97.52546", notes["CMissileTurretEntity::updateLogic accepted reorder"])
        self.assertIn("1 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0", notes["Whole-build gate for 35e7c39"])
        self.assertIn("position2 and position3", notes["CMissileTurretEntity::updateLogic named temporaries"])
        self.assertIn("97.52546 to 97.90278", notes["CMissileTurretEntity::updateLogic named temporaries"])
        self.assertIn("32 candidates", notes["CMissileTurretEntity::findPriorityAlien permutation"])
        self.assertIn("existing best = 0", notes["CMissileTurretEntity::findPriorityAlien permutation"])
        self.assertIn("97.24 to 97.90", notes["Second whole-build gate"])


class WinsockNotifyReceiveResidualTests(unittest.TestCase):
    def test_authored_receive_recovery_and_remaining_residuals_are_recorded(self):
        idiom = next(
            item for item in knowledge.load("gcc-4.4.3")
            if item.id == "network-recovery-residuals"
        )
        note = next(
            item["note"] for item in idiom.evidence
            if item.get("function") == "CWinsockNetworkDevice::notifyReceive"
        )

        for fact in (
            "65.34",
            "improved the function score from 71.63 to 75.86",
            "improved it to 95.80",
            "0x208",
            "5,000 cases",
            "EET_NETWORK_EVENT",
            "C1/C2",
            "86.7",
            "1 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0",
        ):
            self.assertIn(fact, note)


class StaticTextBreakTextEvidenceTests(unittest.TestCase):
    def test_loop_oracle_and_exact_match_failures_are_recorded(self):
        idiom = next(
            item for item in knowledge.load("gcc-4.4.3")
            if item.id == "igui-static-text-breaktext-residual"
        )
        notes = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertEqual(idiom.status, "hypothesis")
        self.assertIn("506 block-order rows", notes["IGUIStaticText::breakText"])
        self.assertIn("246 of 500 cases", idiom.limits)
        self.assertIn("no further ancestor-shaped edit was tried", idiom.limits)
        for fact in ("34.03512 to 71.4539", "51.019802 to 37.0099", "1,679.5229 score-bytes", "500 of 500"):
            self.assertIn(fact, notes["Full Irrlicht-shaped loop"])
        for fact in ("100 to 99.94827", "100 to 99.788734", "500 of 500"):
            self.assertIn(fact, notes["Loop-only form"])

    def test_seed_context_and_residual_snapshot_is_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "harvest-missile-turret-unit-residuals")
        note = next(
            item["note"] for item in idiom["evidence"]
            if item.get("function") == "Harvest 35e7c39 unit context and residuals"
        )

        for fact in (
            "30 emitted inline and template copies, all 30 placed",
            "12 Mac-map rows without emitted matches",
            "an unnamed 4-byte target extent at 0x435fb0",
            "0 called sections verified",
            "statics finds four objects, all same-layout",
            "0 locked functions and 0 witnesses",
            "CMissileEntity::updateLogic target 2930, candidate 2954",
            "CMissileTurretEntity::updateLogic target and candidate 1842",
            "findPriorityAlien target 1484, candidate 1485",
            "render target and candidate 163",
            "signedness finds 0 aligned pairs",
        ):
            self.assertIn(fact, note)


class DropshipUnitEvidenceTests(unittest.TestCase):
    def test_logic_guidance_preserves_context_and_assembly_constraints(self):
        idioms = knowledge.for_causes(knowledge.load("gcc-4.4.3"), {Cause.BLOCK_ORDER})
        idiom = next(item for item in idioms if item.id == "dropship-logic-lifetimes")
        evidence = {item["function"]: item["note"] for item in idiom.evidence}

        for fact in ("21 emitted", "53/55", "31 declaration-count phases", "same-layout"):
            self.assertIn(fact, evidence["Unit context"])
        for fact in ("0x42b890", "5962", "83.263985", "84.0201", "15.0f"):
            self.assertIn(fact, evidence["CDropshipEntity::updateLogic landing"])
        self.assertIn("DragonFly0019", evidence["CDropshipEntity::loadSprite"])
        self.assertIn("0 worse", evidence["Whole-build gate"])
        self.assertIn("unresolved", idiom.limits)
        self.assertIn("128", idiom.limits)
        self.assertIn("5 to 10", evidence["Rejected search"])
        self.assertIn("15 differing", evidence["Rejected search"])


class HighscoreUnitResidualTests(unittest.TestCase):
    def test_rejected_source_forms_and_unit_context_are_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "harvest-highscore-unit-residuals")
        notes = {item["function"]: item["note"] for item in idiom["evidence"]}

        self.assertEqual(idiom["status"], "hypothesis")
        self.assertIn("10 emitted inline and template copies, all 10 placed", notes["Unit context"])
        self.assertIn("63.147266 to 99.81066", notes["parseHighscoreString"])
        self.assertIn("4,928 to 4,784", notes["parseHighscoreString"])
        self.assertIn("89.835526 to 89.5", notes["setVisible"])
        self.assertIn("A 32-candidate unit-scoped permutation search finds no score gain", notes["OnEvent"])
        self.assertIn("31 candidates, finds no rewrite", notes["update and parseSummaryPage"])
        self.assertIn("0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0", notes["Unchanged seed gate"])
        evidence = {item["function"]: item["note"] for item in idiom["evidence"]}
        self.assertIn("www.oxeyegames.com moves from +0x390 to +0x578", evidence["Pooled literal sections"])
        self.assertIn("40 differing aligned rows", evidence["loadHighscores instruction differences"])
        self.assertIn("five differing aligned rows", evidence["loadSummaryPage instruction differences"])
        self.assertIn("does not correct the emitted data or instructions", evidence["Definition-order overlay"])
        self.assertIn("saves 5,240 functions and 1,631 matched data runs", evidence["Clean source gate"])

    def test_literal_type_keys_on_after_encore_seed_record_the_negative_unit_net(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "shared-tail-call-count")
        note = next(
            item["note"] for item in idiom["evidence"]
            if item["function"] == "Literal type keys on after-encore seed"
        )

        self.assertIn("size-weighted score falls by 57.281 target-byte units", note)
        self.assertIn("Matched data bytes remain 4,928", note)


class DaisyGuiCallDistributionTests(unittest.TestCase):
    def test_residual_calls_scores_and_rejected_overlays_are_recorded(self):
        idiom = next(
            item for item in knowledge.load("gcc-4.4.3")
            if item.id == "daisy-gui-call-distribution"
        )
        notes = {item["function"]: item["note"] for item in idiom.evidence}

        self.assertEqual(idiom.status, "hypothesis")
        for name in (
            "CGUITextButton::CGUITextButton",
            "CGUITabControl::CGUITabControl",
            "CGUIRadioList::CGUIRadioList",
            "CGUIContextMenu::CGUIContextMenu",
            "CGUIContextMenu::~CGUIContextMenu",
            "CGUIButton::~CGUIButton",
        ):
            self.assertIn(name, notes["Six residual functions"])
        for score in ("80.41", "83.75", "79.19", "81.70", "67.95", "72.96"):
            self.assertIn(score, notes["Six residual functions"])
        self.assertIn("zero gains", notes["Shared header overlays"])
        self.assertIn("Neither inexact candidate received an oracle check", idiom.limits)
        self.assertIn("0 worse; data bytes matched: +0 -0", notes["Baseline whole-build gate"])


class SceneCollisionManagerResidualTests(unittest.TestCase):
    def test_scene_collision_residuals_record_source_checks_and_rejected_forms(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "daisy-scene-collision-manager-residuals")
        notes = {item["function"]: item["note"] for item in idiom["evidence"]}

        self.assertEqual(idiom["cause"], "register-allocation")
        self.assertEqual(idiom["status"], "hypothesis")
        self.assertIn("28.49", idiom["limits"])
        self.assertIn("256 candidates", idiom["limits"])
        self.assertIn("77.45152 to 75.93629", idiom["limits"])
        self.assertIn("0 differ", notes["calls and constants"])
        self.assertIn("0 locked functions and 0 witnesses", notes["Unit context"])
        self.assertIn("92.37", notes["collideWithWorld"])
        self.assertIn("0 functions changed: 0 newly matched, 0 worse; data bytes matched: +0 -0", notes["Whole-build gate"])


class ScenarioEventInliningKnowledgeTests(unittest.TestCase):
    def test_scenario_credit_reference_counts_are_recorded_as_inline_distribution(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "inlined-string-reference-counts")
        evidence = next(
            (item for item in idiom["evidence"] if item.get("function") == "CScenario::createScenarioEvents at Harvest a10b927"),
            None,
        )

        self.assertIsNotNone(evidence)
        for fact in (
            "Jens has 3 candidate references and 1 target reference",
            "Jonas has 1 candidate reference and 3 target references",
            "The target passes Jens to the out-of-line constructor and inlines Jonas",
            "the candidate inlines Jens and passes Jonas out of line",
            "256 unit-scoped permutation candidates at depth 3 leave the score at 78.80",
        ):
            self.assertIn(fact, evidence["note"])


class HarvestGameLayerResidualTests(unittest.TestCase):
    def test_game_layer_data_and_gate_evidence_is_recorded(self):
        path = Path(__file__).parents[1] / "src/concord/knowledge/gcc-4.4.3.toml"
        data = tomllib.loads(path.read_text())
        idiom = next(item for item in data["idiom"] if item["id"] == "harvest-game-layer-data-residuals")
        notes = {item["function"]: item["note"] for item in idiom["evidence"]}

        self.assertEqual(idiom["cause"], "block-order")
        self.assertEqual(idiom["status"], "hypothesis")
        self.assertIn("45.85%", idiom["limits"])
        self.assertIn("56.09%", idiom["limits"])
        self.assertIn("87.54417 to 89.14984", idiom["limits"])
        self.assertIn("0 bases differ", notes["CWorld::update field pairing"])
        self.assertIn("5,000 of 5,000 cases", notes["CWorld::update rejected source forms"])
        self.assertIn("data bytes matched: +0 -11", notes["CWorld::update rejected source forms"])


class HarvestOutOfLineExtentTests(unittest.TestCase):
    def test_rejected_extent_candidates_record_their_gate_evidence(self):
        idioms = {item.id: item for item in knowledge.load("gcc-4.4.3")}

        missile = idioms["harvest-missile-add-kill-count-out-of-line"]
        missile_notes = {item["function"]: item["note"] for item in missile.evidence}
        self.assertEqual(missile.status, "hypothesis")
        self.assertIn("0x435fb0", missile.symptom)
        for fact in ("100.0", "99.70588", "cmp rax, rbx", "cmp rbx, rax"):
            self.assertIn(fact, missile_notes["Dependent CEntityManager constructor"])

        priorities = idioms["harvest-alien-priorities-out-of-line"]
        priority_notes = {item["function"]: item["note"] for item in priorities.evidence}
        self.assertEqual(priorities.status, "hypothesis")
        self.assertIn("C1", priority_notes["Exact candidate extents"])
        self.assertIn("C2", priority_notes["Exact candidate extents"])
        self.assertIn("D2", priority_notes["Exact candidate extents"])
        for fact in ("90.13893", "90.123665", "T.358"):
            self.assertIn(fact, priority_notes["Dependent units"])

        image = idioms["cimage-copy-constructor-target-extents"]
        image_notes = {item["function"]: item["note"] for item in image.evidence}
        self.assertEqual(image.status, "hypothesis")
        for fact in ("392", "382", "0x4deb30", "0x4de9b0"):
            self.assertIn(fact, image_notes["Current ABI copies"])


if __name__ == "__main__":
    unittest.main()
