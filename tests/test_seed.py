"""Finding functions a seeded unit and Harvest's port both define."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.harvest import BUILD
from concord.seed import port_conflicts, replace_in_port

UNIT = b"""namespace daisy { namespace video {
struct ILoader { virtual ~ILoader() {} };
struct CLoaderPSD : ILoader { bool load(int x); };
bool CLoaderPSD::load(int x) { return x > 0; }
ILoader* createLoaderPSD() { return new CLoaderPSD(); }
} }
"""
PORT = b"""namespace daisy { namespace video {
namespace { struct CNull : ILoader { bool load(int) { return false; } }; }
ILoader* createLoaderPSD()
{
    return new CNull();
}
} }
"""


@unittest.skipUnless(shutil.which("g++") and shutil.which("c++filt"), "needs g++ and c++filt")
class PortConflicts(unittest.TestCase):
    def test_a_port_stub_of_a_free_function_is_found_and_same_named_members_are_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "port" / "src").mkdir(parents=True)
            (root / "port" / "src" / "Stubs.cpp").write_bytes(PORT)
            out = root / "build" / "match" / BUILD
            out.mkdir(parents=True)
            (root / "unit.cpp").write_bytes(UNIT)
            subprocess.run(["g++", "-c", str(root / "unit.cpp"), "-o", str(out / "daisy__video__CLoaderPSD.o")], check=True)
            self.assertEqual(
                port_conflicts(root, "daisy/video/CLoaderPSD.cpp"),
                ["daisy::video::createLoaderPSD() in port/src/Stubs.cpp"],
            )

    def test_a_conflicting_unit_is_listed_as_replaced_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "port" / "src").mkdir(parents=True)
            (root / "port" / "src" / "Stubs.cpp").write_bytes(PORT)
            out = root / "build" / "match" / BUILD
            out.mkdir(parents=True)
            (root / "unit.cpp").write_bytes(UNIT)
            subprocess.run(["g++", "-c", str(root / "unit.cpp"), "-o", str(out / "daisy__video__CLoaderPSD.o")], check=True)
            build = root / "port" / "build.zig"
            build.write_text('const replaced = [_][]const u8{\n    // The blocking main loop.\n    "HarvestFull/main.cpp",\n};\n')
            self.assertTrue(replace_in_port(root, "daisy/video/CLoaderPSD.cpp"))
            text = build.read_text()
            entry = '    // Recovered from Irrlicht 0.7; the port keeps its own port/src/Stubs.cpp.\n    "daisy/video/CLoaderPSD.cpp",\n};'
            self.assertIn('    "HarvestFull/main.cpp",\n' + entry, text)
            self.assertFalse(replace_in_port(root, "daisy/video/CLoaderPSD.cpp"))
            self.assertEqual(build.read_text(), text)


if __name__ == "__main__":
    unittest.main()
