"""Comparing snapshots of Harvest's progress report, in the shape hv.progress writes."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from concord.scores import compare, normalized, snapshot


def function(name, address, size, fuzzy, matched):
    m = {"total_code": str(size), "matched_code": str(size if matched else 0), "total_data": "0", "matched_data": "0"}
    f = {"name": name, "size": str(size), "fuzzy_match_percent": fuzzy,
         "metadata": {"demangled_name": name, "virtual_address": str(address)}}  # fmt: skip
    return {"name": f"{name} @ {address:#x}", "measures": m, "functions": [f]}


def data(address, size):
    m = {"total_code": "0", "matched_code": "0", "total_data": str(size), "matched_data": str(size)}
    s = {"name": ".rodata", "size": str(size), "fuzzy_match_percent": 100, "metadata": {"virtual_address": str(address)}}
    return {"name": f"Matched .rodata data @ {address:#x}", "measures": m, "sections": [s]}


class Compare(unittest.TestCase):
    def test_disjoint_ranges_are_not_the_owners_contiguous_summary(self):
        # Harvest's owner has two disjoint spans, but its report gives only the
        # first address and their combined size in one section.
        before = snapshot({
            "units": [data(0x1000, 8)],
            "concord_matched_data": [[0x1000, 4], [0x2000, 4]],
        })
        after = snapshot({
            "units": [data(0x1000, 8)],
            "concord_matched_data": [[0x1000, 4], [0x3000, 4]],
        })
        _, lost, gained = compare(before, after)
        self.assertEqual((lost, gained), (4, 4))

    def test_changing_data_owners_does_not_change_matched_addresses(self):
        before = snapshot({
            "units": [data(0x1000, 8)],
            "concord_matched_data": [[0x1000, 4], [0x2000, 4]],
        })
        after = snapshot({
            "units": [data(0x1000, 4), data(0x2000, 4)],
            "concord_matched_data": [[0x1000, 4], [0x2000, 4]],
        })
        _, lost, gained = compare(before, after)
        self.assertEqual((lost, gained), (0, 0))

    def test_functions_by_score_and_data_by_bytes(self):
        before = snapshot({"units": [
            function("f", 0x100, 10, 100.0, True), function("g", 0x200, 10, 70.0, False),
            function("h", 0x300, 10, 50.0, False), data(0x1000, 60), data(0x2000, 8),
        ], "concord_matched_data": [[0x1000, 60], [0x2000, 8]]})  # fmt: skip
        after = snapshot({"units": [
            function("f", 0x100, 10, 100.0, False), function("g", 0x200, 10, 68.0, False),
            function("h", 0x300, 10, 55.0, False),
            data(0x0fa0, 284),  # absorbs the run at 0x1000: no byte lost
        ], "concord_matched_data": [[0x0fa0, 284]]})  # fmt: skip
        changes, lost, gained = compare(before, after)
        self.assertEqual({c.name: c.worse for c in changes}, {"f": True, "g": True, "h": False})
        self.assertEqual((lost, gained), (8, 284 - 60))

    def test_rejects_old_snapshots_whose_data_ranges_are_ambiguous(self):
        current = snapshot({"units": [], "concord_matched_data": []})
        with self.assertRaisesRegex(ValueError, "recapture"):
            compare({"functions": {}, "data": []}, current)


class Normalized(unittest.TestCase):
    def checkout(self, root, configuration_changed):
        subprocess.run(["git", "init", "-q", str(root)], check=True)

        def write(path, text):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

        def commit(message, paths):
            subprocess.run(["git", "-C", str(root), "add", "--", *paths], check=True)
            subprocess.run([
                "git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                "commit", "-qm", message,
            ], check=True)

        write("builds.json", '{"canonical":"test"}')
        write("src/game.cpp", "base source")
        write("third_party/input.txt", "shared dependency")
        write("config/test/units.toml", "units = []\n")
        write("config/test/symbols.tsv", "base symbols\n")
        base_paths = [
            "builds.json", "src/game.cpp", "third_party/input.txt", "config/test/units.toml", "config/test/symbols.tsv",
        ]
        commit("base", base_paths)
        write("src/game.cpp", "head source")
        paths = ["src/game.cpp"]
        if configuration_changed:
            write("config/test/symbols.tsv", "head symbols\n")
            paths.append("config/test/symbols.tsv")
        commit("head", paths)

    def test_separates_source_and_configuration_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "harvest"
            root.mkdir()
            self.checkout(root, configuration_changed=True)

            def captured(checkout, capture=False, ref=None):
                self.assertTrue(capture)
                source = (checkout / "src/game.cpp").read_text()
                symbols = (checkout / "config/test/symbols.tsv").read_text()
                if symbols.startswith("base"):
                    return {"units": [function("f", 0x100, 10, 90.0, False)], "concord_matched_data": []}
                functions = [function("f", 0x100, 10, 100.0, True)]
                if source == "head source":
                    functions.append(function("g", 0x200, 10, 60.0, False))
                return {"units": functions, "concord_matched_data": [[0x1000, 5]]}

            with patch("concord.scores.report", side_effect=captured):
                source_before, source_after, evidence = normalized(root, "HEAD~1")

            source_changes, source_lost, source_gained = compare(source_before, source_after)
            self.assertEqual([change.name for change in source_changes], ["g"])
            self.assertEqual((source_lost, source_gained), (0, 0))
            config_changes, config_lost, config_gained = compare(evidence["configuration_baseline"], source_before)
            self.assertEqual([change.name for change in config_changes], ["f"])
            self.assertEqual((config_lost, config_gained), (0, 5))
            self.assertEqual(evidence["configuration_changes"], ["config/test/symbols.tsv"])

    def test_source_only_change_has_no_configuration_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "harvest"
            root.mkdir()
            self.checkout(root, configuration_changed=False)
            calls = []

            def captured(checkout, capture=False, ref=None):
                calls.append((checkout / "src/game.cpp").read_text())
                return {"units": [function("f", 0x100, 10, 100.0, True)], "concord_matched_data": []}

            with patch("concord.scores.report", side_effect=captured):
                _, _, evidence = normalized(root, "HEAD~1")

            self.assertEqual(calls, ["base source", "head source"])
            self.assertEqual(evidence["configuration_changes"], [])
            self.assertIsNone(evidence["configuration_baseline"])


if __name__ == "__main__":
    unittest.main()
