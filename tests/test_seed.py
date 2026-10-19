"""Finding functions a seeded unit and Harvest's port both define."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from concord import seed as seed_module
from concord.harvest import BUILD
from concord.seed import (
    UNDEFINED,
    SeedResult,
    _declare_members,
    _discard,
    _mac_path,
    _snapshot,
    _stub_failing,
    port_conflicts,
    replace_in_port,
    seed,
)

IRRLICHT_SOURCE = "third_party/irrlicht-0.7/source/Irrlicht"

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


class Discard(unittest.TestCase):
    def test_a_failed_seed_removes_new_files_and_restores_changed_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "d").mkdir(parents=True)
            kept, changed = root / "src" / "d" / "Kept.h", root / "src" / "d" / "Shared.h"
            kept.write_text("kept")
            changed.write_text("as an earlier unit left it")
            before = _snapshot(root)
            changed.write_text("translated again")
            (root / "src" / "d" / "New.cpp").write_text("new")
            _discard(root, SeedResult("d/New.cpp", written=["d/New.cpp", "d/Shared.h"]), before)
            self.assertEqual(changed.read_text(), "as an earlier unit left it")
            self.assertEqual(kept.read_text(), "kept")
            self.assertFalse((root / "src" / "d" / "New.cpp").exists())


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
            self.assertIn(b"A::A(int x)\n{\n    // Not recovered: Irrlicht 0.7's body does not compile against daisy's interfaces.\n    __builtin_trap();\n}", text)
            self.assertIn(b"int A::ok() const\n{\n    return X;\n}", text)
            self.assertIn(b"int A::bad(int n)\n{\n    // Not recovered: Irrlicht 0.7's body does not compile against daisy's interfaces.\n    __builtin_trap();\n}", text)
            self.assertEqual((root / "src" / "d" / "Other.cpp").read_bytes(), source)
            self.assertEqual(_stub_failing(root, output, {"d/A.cpp"}), [])  # a stub is not stubbed again


class CompletionRejected(unittest.TestCase):
    """seed --complete --stub when settle rejects the header completion: the header is
    restored and the unit is still seeded, with the function that needed the member stubbed."""

    IRRLICHT = b"""bool CLoaderPSD::load(int x)
{
    return x > 0;
}

int CLoaderPSD::uses(int x)
{
    return CVector3d::member_use(x);
}
"""
    HEADER = b"struct CVector3d { int x; };\n"

    def test_a_rejected_header_is_restored_and_the_failing_function_is_stubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            irrlicht = root / seed_module.IRRLICHT / "source" / "Irrlicht"
            irrlicht.mkdir(parents=True)
            (irrlicht / "CLoaderPSD.cpp").write_bytes(self.IRRLICHT)
            (root / "src" / "ox" / "core").mkdir(parents=True)
            header = root / "src" / "ox" / "core" / "CVector3d.h"
            header.write_bytes(self.HEADER)
            (root / "src" / "daisy" / "video").mkdir(parents=True)
            target = root / "src" / "daisy" / "video" / "CLoaderPSD.cpp"

            def complete(_root, members):  # the completion adds the member the body needs
                header.write_bytes(self.HEADER + b"// member_use\n")
                return True

            def match(_root, unit, learn):  # the unit fails to compile while the member is absent
                errors = "".join(
                    f"src/daisy/video/CLoaderPSD.cpp:{number}: error: no member_use\n"
                    for number, line in enumerate(target.read_text().splitlines(), 1)
                    if "member_use(" in line and "member_use" not in header.read_text()
                )
                return errors or "functions 2/2"

            with (
                mock.patch.object(seed_module, "translate", lambda text: text),
                mock.patch.object(seed_module, "provenance", lambda text, original: text),
                mock.patch.object(seed_module, "_color_helpers", lambda _root, _text: []),
                mock.patch.object(seed_module, "_has_static_initializer", lambda _root, _unit: False),
                mock.patch.object(seed_module, "_match", match),
                mock.patch.object(seed_module, "Harvest"),
                mock.patch.object(seed_module, "missing_members", lambda output: {("CVector3d", "member_use")}),
                mock.patch.object(seed_module, "complete", complete),
                mock.patch.object(seed_module, "_compiles", lambda _root, _unit, _header: True),
                mock.patch.object(seed_module, "_include_incomplete", lambda _root, _source, _output: False),
                mock.patch.object(seed_module, "regressions", lambda *args, **kwargs: ["daisy/scene/CBspTree.cpp CBspTree::build"]),
                mock.patch.object(seed_module, "_register"),
                mock.patch.object(seed_module, "_unregister") as unregister,
                mock.patch.object(seed_module, "port_conflicts", lambda _root, _unit: []),
            ):
                result = seed(root, "daisy/video/CLoaderPSD.cpp", complete_types=True, stub=True)
            self.assertEqual(result.error, "")
            self.assertFalse(unregister.called)
            self.assertEqual(header.read_bytes(), self.HEADER)
            self.assertNotIn("ox/core/CVector3d.h", result.written)
            self.assertEqual(result.stubbed, ["daisy/video/CLoaderPSD.cpp:6 CLoaderPSD::uses"])
            self.assertEqual((result.exact, result.functions), (2, 2))


class DeclareMembers(unittest.TestCase):
    """A provisional daisy class header gets the member declarations its unit needs,
    copied from Irrlicht's class, and keeps its placeholder that sets the size."""

    IRRLICHT = """namespace irr {
namespace io {
class CTextReader : public IUnknown
{
public:
    CTextReader(IReadFile* file);
    virtual ~CTextReader();
    wchar_t* getText();
    int getTextSize() const;
private:
    void readFile(IReadFile* file);
    char* CData;
};
}
}
"""
    HARVEST = """#ifndef DAISY_IO_CTEXTREADER_H
#define DAISY_IO_CTEXTREADER_H
namespace daisy {
namespace io {
class CTextReader : public ox::IUnknown
{
public:
    CTextReader(ox::io::IReadFile* file);
private:
    char Unrecovered[0x30 - sizeof(ox::IUnknown)];
};
}
}
#endif
"""
    OUTPUT = (
        "src/daisy/io/CTextReader.cpp:3: error: no 'wchar_t* daisy::io::CTextReader::getText()' member function declared in class 'daisy::io::CTextReader'\n"
        "src/daisy/io/CTextReader.cpp:9: error: definition of implicitly-declared 'virtual daisy::io::CTextReader::~CTextReader()'\n"
    )

    def test_only_the_missing_declarations_are_copied_and_the_placeholder_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CTextReader.h").write_text(self.IRRLICHT)
            (root / "src" / "daisy" / "io").mkdir(parents=True)
            header = root / "src" / "daisy" / "io" / "CTextReader.h"
            header.write_text(self.HARVEST)
            originals = {}
            self.assertTrue(_declare_members(root, self.OUTPUT, originals))
            text = header.read_text()
            self.assertIn("public:\n    virtual ~CTextReader();\n    wchar_t* getText();\n    CTextReader(ox::io::IReadFile* file);", text)
            self.assertNotIn("readFile", text)
            self.assertIn("char Unrecovered[0x30 - sizeof(ox::IUnknown)];", text)
            self.assertEqual(originals, {"daisy/io/CTextReader.h": self.HARVEST})
            self.assertFalse(_declare_members(root, self.OUTPUT, {}))  # declared once


class ProvisionalHeaders(unittest.TestCase):
    """A provisional header in the directory the Mac map gives its unit gets the
    base class and member declarations Irrlicht's class has, and keeps its daisy-only
    declarations."""

    IRRLICHT = """namespace irr {
namespace video {
class CImageLoaderJPG : public IImageLoader
{
public:
    CImageLoaderJPG();
    virtual ~CImageLoaderJPG();
    virtual bool isALoadableFileExtension(const char* fileName);
private:
    int unused;
};
}
}
"""
    HARVEST = """// PROVISIONAL: only the JPEG writer is declared.
#ifndef DAISY_VIDEO_NULL_CIMAGELOADERJPG_H
#define DAISY_VIDEO_NULL_CIMAGELOADERJPG_H

namespace ox {
namespace io { class IWriteFile; }
}

namespace daisy {
namespace video {

class CImageLoaderJPG
{
public:
    static bool saveImage(ox::io::IWriteFile* file, unsigned char* data, int width, int height, int channels);
};

}
}

#endif
"""
    OUTPUT = (
        "src/daisy/video/CImageLoaderJPG.cpp:18: error: definition of implicitly-declared 'daisy::video::CImageLoaderJPG::CImageLoaderJPG()'\n"
        "src/daisy/video/CImageLoaderJPG.cpp:27: error: definition of implicitly-declared 'daisy::video::CImageLoaderJPG::~CImageLoaderJPG()'\n"
    )

    def test_the_header_in_the_mac_directory_gets_its_base_and_members_and_keeps_saveimage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CImageLoaderJPG.h").write_text(self.IRRLICHT)
            (root / "src" / "ox" / "video").mkdir(parents=True)
            (root / "src" / "ox" / "video" / "IImageLoader.h").write_text("namespace ox {\nnamespace video {\nclass IImageLoader\n{\n};\n}\n}\n")
            header = root / "src" / "daisy" / "video" / "Null" / "CImageLoaderJPG.h"
            header.parent.mkdir(parents=True)
            header.write_text(self.HARVEST)
            _index_cache_clear()
            originals = {}
            self.assertTrue(_declare_members(root, self.OUTPUT, originals))
            text = header.read_text()
            self.assertIn('#define DAISY_VIDEO_NULL_CIMAGELOADERJPG_H\n#include "../../../ox/video/IImageLoader.h"\n', text)
            self.assertIn("class CImageLoaderJPG : public ox::video::IImageLoader\n{\npublic:\n    CImageLoaderJPG();\n    virtual ~CImageLoaderJPG();\n", text)
            self.assertIn("static bool saveImage(", text)
            self.assertEqual(originals, {"daisy/video/Null/CImageLoaderJPG.h": self.HARVEST})

    def test_a_unit_resolves_to_the_directory_the_mac_map_gives_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reference" / "1.18-mac-i386").mkdir(parents=True)
            (root / "reference" / "1.18-mac-i386" / "symbols.csv").write_text(
                '0x1,"__TEXT,__text",global,daisy/video/Null/CImage.cpp,_Z1f,f()\n'
            )
            self.assertEqual(_mac_path(root, "daisy/video/CImage.cpp"), "daisy/video/Null/CImage.cpp")
            self.assertEqual(_mac_path(root, "daisy/io/CTextReader.cpp"), "daisy/io/CTextReader.cpp")


def _index_cache_clear():
    seed_module._indexes.clear()


if __name__ == "__main__":
    unittest.main()
