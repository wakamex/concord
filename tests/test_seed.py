"""Finding functions a seeded unit and Harvest's port both define."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.harvest import BUILD
from concord.seed import UNDEFINED, _stub_failing, port_conflicts, replace_in_port

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


class PortLink(unittest.TestCase):
    def test_undefined_symbols_are_read_by_the_file_that_references_them(self):
        output = (
            "error: ld.lld: undefined symbol: daisy::scene::CMeshSceneNode::render()\n"
            "    note: referenced by CWaterSurfaceSceneNode.cpp:42 (../src/daisy/scene/CWaterSurfaceSceneNode.cpp:42)\n"
            "    note:               .zig-cache/o/1/CWaterSurfaceSceneNode.o:(vtable for daisy::scene::CWaterSurfaceSceneNode)\n"
        )
        self.assertEqual(UNDEFINED.findall(output), [("daisy::scene::CMeshSceneNode::render()", "CWaterSurfaceSceneNode.cpp")])


class Stubs(unittest.TestCase):
    def test_only_failing_function_bodies_in_written_files_are_stubbed(self):
        source = b"""A::A(int x)
    : X(x), Y(missing(x))
{
    go();
}

int A::ok() const
{
    return X;
}

int A::bad(int n)
{
    return Randomizer::rand() % n;
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "d").mkdir(parents=True)
            (root / "src" / "d" / "A.cpp").write_bytes(source)
            (root / "src" / "d" / "Other.cpp").write_bytes(source)
            output = "src/d/A.cpp:2: error: 'missing' was not declared\nsrc/d/A.cpp:14: error: 'Randomizer' has not been declared\nsrc/d/Other.cpp:14: error: x\n"
            stubbed = _stub_failing(root, output, {"d/A.cpp"})
            text = (root / "src" / "d" / "A.cpp").read_bytes()
            self.assertEqual(stubbed, ["d/A.cpp:1 A::A", "d/A.cpp:12 A::bad"])
            self.assertIn(b"A::A(int x)\n{\n    __builtin_trap();\n}", text)
            self.assertIn(b"int A::ok() const\n{\n    return X;\n}", text)
            self.assertIn(b"int A::bad(int n)\n{\n    __builtin_trap();\n}", text)
            self.assertEqual((root / "src" / "d" / "Other.cpp").read_bytes(), source)
            self.assertEqual(_stub_failing(root, output, {"d/A.cpp"}), [])  # a stub is not stubbed again


if __name__ == "__main__":
    unittest.main()
