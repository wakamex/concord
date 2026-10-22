"""Finding functions a seeded unit and Harvest's port both define."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from concord import seed as seed_module
from concord.harvest import BUILD
from concord.irrlicht import translate
from concord.seed import (
    UNDEFINED,
    SeedResult,
    _color_helpers,
    _declare_members,
    _discard,
    _include_incomplete,
    _includes,
    _index,
    _mac_path,
    _rodata_reference,
    _snapshot,
    _stub_failing,
    _tidy,
    _whole_class,
    port_conflicts,
    port_unresolved,
    qualify,
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
    def test_a_failing_body_keeps_a_base_initializer_list_that_compiles(self):
        source = b"""A::A(int x)
    : X(x)
{
    go(missing);
}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "d").mkdir(parents=True)
            (root / "src" / "d" / "A.cpp").write_bytes(source)
            _stub_failing(root, "src/d/A.cpp:4: error: 'missing' was not declared\n", {"d/A.cpp"})
            text = (root / "src" / "d" / "A.cpp").read_bytes()
            self.assertTrue(text.startswith(b"A::A(int x)\n    : X(x)\n{\n    // Not recovered"), text)

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
                mock.patch.object(seed_module, "_compile_errors", lambda _root, _header: ""),
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

    def test_a_placeholder_class_is_replaced_by_irrlicht_class_with_its_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CTextReader.h").write_text(self.IRRLICHT.replace("    ", "\t"))
            (root / "src" / "daisy" / "io").mkdir(parents=True)
            header = root / "src" / "daisy" / "io" / "CTextReader.h"
            header.write_text(self.HARVEST)
            originals = {}
            self.assertTrue(_declare_members(root, self.OUTPUT, originals))
            text = header.read_text()
            self.assertIn("class CTextReader : public ox::IUnknown\n", text)
            self.assertNotIn("\t", text)
            self.assertIn("    char* CData;\n};", text)
            self.assertIn("#ifdef HARVEST_PORT\nclass CTextReader", text)
            self.assertIn("char Unrecovered[0x30 - sizeof(ox::IUnknown)];", text)  # the port's stand-in, kept under HARVEST_PORT
            self.assertEqual(originals, {"daisy/io/CTextReader.h": self.HARVEST})
            self.assertFalse(_declare_members(root, self.OUTPUT, {}))  # replaced once


class WholeHeaderIncludes(unittest.TestCase):
    def test_a_replaced_class_gets_the_ox_header_of_each_type_it_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "ox" / "core").mkdir(parents=True)
            (root / "src" / "ox" / "core" / "TArray.h").write_text("namespace ox {\nnamespace core {\ntemplate <class T>\nclass TArray\n{\n};\n}\n}\n")
            (root / "src" / "daisy" / "io").mkdir(parents=True)
            seed_module._indexes.clear()
            text = qualify(
                root,
                '#include "ox/io/IXMLReader.h"\nnamespace daisy {\nnamespace io {\nclass X\n{\n    ox::core::TArray<int> Items;\n};\n}\n}\n',
                "daisy/io/X.h",
            )
            self.assertIn('#include "ox/core/TArray.h"', text)


class CompanionHeaders(unittest.TestCase):
    def test_a_units_own_adapted_header_is_translated_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CFoo.h").write_text("namespace irr {\nnamespace scene {\nclass CFoo\n{\npublic:\n    int value();\n};\n}\n}\n")
            (root / "src" / "daisy" / "scene").mkdir(parents=True)
            source = root / "src" / "daisy" / "scene" / "CFoo.cpp"
            source.write_text('#include "CFoo.h"\n')
            header = root / "src" / "daisy" / "scene" / "CFoo.h"
            header.write_text("// Adapted from Irrlicht 0.7 source/Irrlicht/CFoo.h\nold text\n")
            seed_module._indexes.clear()
            written = seed_module._headers(root, source, source.read_text(), set())
            self.assertEqual(written, ["daisy/scene/CFoo.h"])
            self.assertIn("class CFoo", header.read_text())
            self.assertNotIn("old text", header.read_text())


class ReplacedInterfaces(unittest.TestCase):
    def test_a_replaced_ox_class_keeps_its_names_bare_inside_the_namespace(self):
        irrlicht = "namespace irr {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\npublic:\n    virtual bool read() = 0;\n};\n}\n}\n"
        text = "namespace ox {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\n};\n}\n}\n"
        with tempfile.TemporaryDirectory() as tmp:
            out = _whole_class(Path(tmp), text, "IXMLReader", translate(irrlicht, "ox"))
        self.assertIn("class IXMLReader : public ox::IUnknown\n", out)
        self.assertNotIn("class ox::io::IXMLReader", out)
        self.assertIn("virtual bool read() = 0;", out)

    def test_an_include_of_the_same_file_name_counts_whatever_its_path(self):
        self.assertTrue(_includes('#include "../IUnknown.h"\n', "ox/IUnknown.h"))
        self.assertTrue(_includes('#include "ox/IUnknown.h"\n', "ox/IUnknown.h"))
        self.assertFalse(_includes('#include "IUnknownList.h"\n', "ox/IUnknown.h"))

    def test_a_replaced_daisy_class_also_completes_the_ox_interface_it_includes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CXMLReader.h").write_text("namespace irr {\nnamespace io {\nclass CXMLReader : public IXMLReader\n{\npublic:\n    virtual bool read();\n};\n}\n}\n")
            (root / "third_party/irrlicht-0.7/include/IXMLReader.h").write_text("namespace irr {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\npublic:\n    virtual bool read() = 0;\n};\n}\n}\n")
            (root / "src/ox/io").mkdir(parents=True)
            (root / "src/ox/io/IXMLReader.h").write_text("// Partial and provisional: the game never uses XML.\nnamespace ox {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\n};\n}\n}\n")
            (root / "src/daisy/io").mkdir(parents=True)
            header = root / "src/daisy/io/CXMLReader.h"
            header.write_text('#include "ox/io/IXMLReader.h"\nnamespace daisy {\nnamespace io {\nclass CXMLReader : public ox::io::IXMLReader\n{\npublic:\n    CXMLReader();\nprivate:\n    char Unrecovered[8];\n};\n}\n}\n')
            originals = {}
            seed_module._indexes.clear()
            output = "src/daisy/io/CXMLReader.cpp:9: error: no 'bool daisy::io::CXMLReader::read()' member function declared in class 'daisy::io::CXMLReader'\n"
            self.assertTrue(_declare_members(root, output, originals))
            self.assertIn("virtual bool read() = 0;", (root / "src/ox/io/IXMLReader.h").read_text())
            self.assertEqual(originals["ox/io/IXMLReader.h"], "// Partial and provisional: the game never uses XML.\nnamespace ox {\nnamespace io {\nclass IXMLReader : public IUnknown\n{\n};\n}\n}\n")
            self.assertIn("virtual bool read()", header.read_text())

    def test_an_included_ox_interface_gets_its_additional_base_and_destructor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/ICameraSceneNode.h").write_text(
                "namespace irr {\nnamespace scene {\nclass ICameraSceneNode : public ISceneNode, public IEventReceiver\n{\npublic:\n    ICameraSceneNode(ISceneNode* parent) : ISceneNode(parent) {}\n    virtual ~ICameraSceneNode() {};\n    virtual int getMeshType() const { return 0; }\n};\n}\n}\n"
            )
            (root / "src/ox/event").mkdir(parents=True)
            (root / "src/ox/event/IEventReceiver.h").write_text("namespace ox {\nnamespace event {\nclass IEventReceiver\n{\n};\n}\n}\n")
            (root / "src/ox/scene").mkdir(parents=True)
            (root / "src/ox/scene/ISceneNode.h").write_text("namespace ox {\nnamespace scene {\nclass ISceneNode\n{\n};\n}\n}\n")
            ox_header = root / "src/ox/scene/ICameraSceneNode.h"
            ox_header.write_text(
                '// Adapted from Irrlicht 0.7 include/ICameraSceneNode.h (license: third_party/irrlicht-0.7/include/irrlicht.h).\n#include "ISceneNode.h"\n#include "../event/IEventReceiver.h"\nnamespace ox {\nnamespace scene {\nclass ICameraSceneNode : public ISceneNode\n{\npublic:\n    ICameraSceneNode(ISceneNode* parent)\n        : ISceneNode(parent)\n    {\n    }\n};\n}\n}\n'
            )
            (root / "src/daisy/scene").mkdir(parents=True)
            (root / "src/daisy/scene/CCameraSceneNode.h").write_text(
                '#include "ox/scene/ICameraSceneNode.h"\nclass CCameraSceneNode : public ox::scene::ICameraSceneNode\n{\n};\n'
            )
            originals = {}
            seed_module._indexes.clear()
            self.assertTrue(seed_module._complete_interfaces(root, ["daisy/scene/CCameraSceneNode.h"], originals))
            text = ox_header.read_text()
            self.assertIn("class ICameraSceneNode : public ISceneNode, public ox::event::IEventReceiver", text)
            self.assertIn("virtual ~ICameraSceneNode() {}", text)
            self.assertIn("virtual int getMeshType() const\n    {\n        return 0;\n    }", text)
            self.assertIn("ox/scene/ICameraSceneNode.h", originals)


class SettleTogether(unittest.TestCase):
    def test_headers_that_settle_together_are_kept_together(self):
        originals = {"ox/io/IXMLWriter.h": "a", "daisy/io/CXMLWriter.h": "b"}
        with mock.patch.object(seed_module, "Harvest"), mock.patch.object(seed_module, "regressions", lambda harvest, changed, allowed=frozenset(): [] if len(changed) == 2 else ["bad"]):
            self.assertEqual(seed_module._settle_headers(Path("."), originals, frozenset()), (["daisy/io/CXMLWriter.h", "ox/io/IXMLWriter.h"], []))

    def test_a_pair_that_does_not_settle_is_tried_one_header_at_a_time(self):
        originals = {"ox/io/IXMLWriter.h": "a", "daisy/io/CXMLWriter.h": "b"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/io").mkdir(parents=True)
            (root / "src/daisy/io").mkdir(parents=True)
            (root / "src/ox/io/IXMLWriter.h").write_text("changed")
            (root / "src/daisy/io/CXMLWriter.h").write_text("changed")
            with mock.patch.object(seed_module, "Harvest"), mock.patch.object(seed_module, "regressions", lambda harvest, changed, allowed=frozenset(): ["bad"] if len(changed) == 2 or "daisy/io/CXMLWriter.h" in changed else []):
                kept, notes = seed_module._settle_headers(root, originals, frozenset())
            self.assertEqual(kept, ["ox/io/IXMLWriter.h"])
            self.assertEqual(notes, ["completing daisy/io/CXMLWriter.h makes bad worse"])
            self.assertEqual((root / "src/daisy/io/CXMLWriter.h").read_text(), "b")
            self.assertEqual((root / "src/ox/io/IXMLWriter.h").read_text(), "changed")


class IncompleteAndNamespaces(unittest.TestCase):
    def test_an_incomplete_daisy_type_gets_its_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/daisy/io").mkdir(parents=True)
            (root / "src/daisy/io/CTextReader.h").write_text("namespace daisy {\nnamespace io {\nclass CTextReader\n{\n};\n}\n}\n")
            unit = root / "src/daisy/io/CXMLReader.cpp"
            unit.write_text('#include "CXMLReader.h"\n')
            seed_module._indexes.clear()
            self.assertTrue(_include_incomplete(root, unit, "src/daisy/io/CXMLReader.cpp:3: error: invalid use of incomplete type 'class daisy::io::CTextReader'\n"))
            self.assertIn('#include "daisy/io/CTextReader.h"', unit.read_text())

    def test_a_name_in_a_nested_namespace_of_a_top_level_header_is_not_a_top_level_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/video").mkdir(parents=True)
            (root / "src/ox/IOxDevice.h").write_text("namespace ox {\nnamespace video {\nenum E_DRIVER_TYPE\n{\n    EDT_NULL = 0,\n    EDT_SOFTWARE\n};\n}\n}\n")
            seed_module._indexes.clear()
            top = _index(root, root / "src/ox", recursive=False)
            self.assertNotIn("EDT_SOFTWARE", top.enumerators)
            video = _index(root, root / "src/ox/video")
            self.assertEqual(video.headers.get("EDT_SOFTWARE"), "ox/IOxDevice.h")
            self.assertIn("EDT_SOFTWARE", video.enumerators)

    def test_a_daisy_class_a_header_uses_is_included_when_the_header_lacks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/daisy/video/Null").mkdir(parents=True)
            (root / "src/daisy/video/Software").mkdir(parents=True)
            (root / "src/daisy/video/Null/CImage.h").write_text("namespace daisy {\nnamespace video {\nclass CImage\n{\n};\n}\n}\n")
            seed_module._indexes.clear()
            text = qualify(
                root,
                "namespace daisy {\nnamespace video {\nclass CSoftwareTexture\n{\n    CImage* getImage();\n};\n}\n}\n",
                "daisy/video/Software/CSoftwareTexture.h",
            )
            self.assertIn('#include "daisy/video/Null/CImage.h"', text)


class PortListsAndAlgo(unittest.TestCase):
    def test_a_unit_under_a_listed_prefix_is_not_listed_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "port").mkdir(parents=True)
            build = root / "port" / "build.zig"
            build.write_text('const replaced = [_][]const u8{\n    "daisy/scene/CBspTree.cpp",\n};\n')
            self.assertFalse(replace_in_port(root, "daisy/scene/CBspTree.cpp", "reason"))
            self.assertEqual(build.read_text().count("CBspTree.cpp"), 1)

    def test_an_ox_algo_use_includes_the_array_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/algo").mkdir(parents=True)
            (root / "src/ox/algo/CArrayFunctions.h").write_text("namespace ox {\nnamespace algo {\n}\n}\n")
            (root / "src/daisy/scene").mkdir(parents=True)
            seed_module._indexes.clear()
            text = qualify(root, "namespace daisy {\nnamespace scene {\nvoid f(int* a) { ox::algo::sort(a, a + 1); }\n}\n}\n", "daisy/scene/X.cpp")
            self.assertIn('#include "ox/algo/CArrayFunctions.h"', text)
            rand_text = qualify(root, "namespace daisy {\nnamespace scene {\nint f() { return ox::algo::CRand::rand(); }\n}\n}\n", "daisy/scene/X.cpp")
            self.assertIn('#include "ox/algo/CRand.h"', rand_text)

    def test_a_std_algorithm_call_includes_algorithm_unless_the_array_functions_are_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/daisy/scene").mkdir(parents=True)
            seed_module._indexes.clear()
            text = qualify(
                root,
                "namespace daisy {\nnamespace scene {\nvoid f(int* a, int* b, int v) { std::find(a, b, v); }\n}\n}\n",
                "daisy/scene/X.cpp",
            )
            self.assertIn("#include <algorithm>", text)
            seed_module._indexes.clear()
            text = qualify(
                root,
                'namespace daisy {\nnamespace scene {\nvoid f(int* a, int* b, int v) { std::find(a, b, v); ox::algo::sort(a, b); }\n}\n}\n',
                "daisy/scene/X.cpp",
            )
            self.assertIn('#include "ox/algo/CArrayFunctions.h"', text)  # it includes <algorithm> itself
            self.assertNotIn("#include <algorithm>", text)

    def test_a_nine_argument_s3dvertex_constructor_in_a_cpp_file_includes_s3dvertexinline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/daisy/scene").mkdir(parents=True)
            seed_module._indexes.clear()
            text = qualify(
                root,
                '#include "ox/video/S3DVertex.h"\nnamespace daisy {\nnamespace scene {\nvoid f() { ox::video::S3DVertex v(0,0,0, -1,-1,-1, ox::video::SColor(255,255,255,255), 0, 1); }\n}\n}\n',
                "daisy/scene/CTestSceneNode.cpp",
            )
            self.assertIn('#include "ox/video/S3DVertexInline.h"', text)


class SettleCompletion(unittest.TestCase):
    """A completed member can itself call a member Harvest's headers lack, as
    S3DVertex's operator== compares SColor fields; the member completes too."""

    IRR_VERTEX = """namespace irr {
namespace video {
struct S3DVertex
{
	bool operator == (const S3DVertex& other) const
	{
		return (Color == other.Color);
	}
};
}
}
"""
    IRR_SCOLOR = """namespace irr {
namespace video {
class SColor
{
public:
	bool operator==(const SColor& other) const { return other.color == color; }
};
}
}
"""
    OX_VERTEX = """namespace ox {
namespace video {

struct S3DVertex
{
    S3DVertex() {}
};

}
}
"""
    OX_SCOLOR = """namespace ox {
namespace video {

class SColor
{
public:
    unsigned int color;
};

}
}
"""

    def test_a_member_the_added_member_needs_completes_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/video").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/S3DVertex.h").write_text(self.IRR_VERTEX)
            (root / "third_party/irrlicht-0.7/include/SColor.h").write_text(self.IRR_SCOLOR)
            (root / "src/ox/video/S3DVertex.h").write_text(self.OX_VERTEX)
            scolor = root / "src/ox/video/SColor.h"
            scolor.write_text(self.OX_SCOLOR)

            def compile_errors(_root, header):
                if header == "ox/video/S3DVertex.h" and "operator==" not in scolor.read_text():
                    return "x.h:2: error: no match for 'operator==' (operand types are 'ox::video::SColor' and 'const ox::video::SColor')"
                return ""

            errors = "x.h:4: error: no match for 'operator==' (operand types are 'ox::video::SColor' and 'const ox::video::SColor')"
            with mock.patch.object(seed_module, "_compile_errors", compile_errors):
                seed_module.complete(root, {("S3DVertex", "operator==")})
                originals = {}
                self.assertTrue(
                    seed_module._settle_completion(root, "ox/video/S3DVertex.h", "S3DVertex", errors, originals)
                )
            self.assertIn("bool operator==(const SColor& other) const", scolor.read_text())
            self.assertEqual(sorted(originals), ["ox/video/SColor.h"])

    def test_a_completion_that_cannot_settle_is_reported_unsettled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/video").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/S3DVertex.h").write_text(self.IRR_VERTEX)
            vertex = root / "src/ox/video/S3DVertex.h"
            vertex.write_text(self.OX_VERTEX)
            errors = "x.cpp:9: error: no match for 'operator==' (operand types are 'ox::video::S3DVertex' and 'const ox::video::S3DVertex')"
            with mock.patch.object(seed_module, "_compile_errors", lambda _root, _header: "still fails"):
                self.assertFalse(
                    seed_module._settle_completion(root, "ox/video/S3DVertex.h", "S3DVertex", errors, {})
                )


class RollbackConfig(unittest.TestCase):
    def test_a_failed_seed_restores_the_target_config_it_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config" / BUILD).mkdir(parents=True)
            symbols = root / "config" / BUILD / "symbols.tsv"
            symbols.write_text("0x1\t4\tf\tmatch:d/f.cpp\n")
            present = _snapshot(root)
            symbols.write_text("0x1\t4\tf\tmatch:d/f.cpp\n0x2\t4\tg\tmatch:d/f.cpp\n")
            _discard(root, SeedResult("d/f.cpp"), present)
            self.assertEqual(symbols.read_text(), "0x1\t4\tf\tmatch:d/f.cpp\n")


@unittest.skipUnless(shutil.which("as"), "needs as")
class RodataReference(unittest.TestCase):
    ASSEMBLY = """    .text
    .globl f
    .type f, @function
f:
    movq table(,%rdi,8), %rax
    ret
    .size f, .-f
    .section .rodata
table:
    .quad 1, 2, 3
"""

    def test_a_relocation_into_rodata_is_found_with_the_function_holding_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "build" / "match" / BUILD
            out.mkdir(parents=True)
            (root / "f.s").write_text(self.ASSEMBLY)
            subprocess.run(["as", str(root / "f.s"), "-o", str(out / "d__f.o")], check=True)
            offset, _, start, size = _rodata_reference(root, "d/f.cpp")
            self.assertEqual((start, size), (0, 9))
            self.assertTrue(start <= offset < start + size)

    def test_an_unplaced_template_section_called_from_an_fde_sized_function_is_placed(self):
        import json

        unit_asm = """    .section .text.helper,"axG",@progbits,helper,comdat
    .weak helper
    .type helper, @function
helper:
    xorl %eax, %eax
    ret
    .size helper, .-helper
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "build" / "match" / BUILD
            out.mkdir(parents=True)
            (root / "u.s").write_text(unit_asm)
            subprocess.run(["as", str(root / "u.s"), "-o", str(out / "d__f.o")], check=True)
            (out / "d__f.json").write_text(
                json.dumps(
                    {
                        "unplaced_sections": [".text.helper"],
                        "sections": [
                            {
                                "name": ".text",
                                "address": "0x100",
                                "bad_references": [{"symbol": "helper", "candidate": "0x300"}],
                            },
                        ],
                    }
                )
            )
            with mock.patch.object(seed_module, "_extents", lambda _root: [(0x300, 3)]):
                self.assertEqual(seed_module._place_called_sections(root, "d/f.cpp"), {".text.helper": 0x300})



class TidyFiles(unittest.TestCase):
    def test_a_file_ends_after_its_last_code_line_with_one_newline(self):
        self.assertEqual(_tidy("class C\n{\n};\n\n\n"), "class C\n{\n};\n")
        self.assertEqual(_tidy("class C\n{\n};"), "class C\n{\n};\n")


class PortGuard(unittest.TestCase):
    STAND_IN = "namespace daisy {\nclass CTextReader\n{\npublic:\n    char Unrecovered[8];\n};\n}\n"
    IRRLICHT = "namespace irr {\nclass CTextReader\n{\npublic:\n    int getText();\n};\n}\n"

    def test_a_replaced_class_keeps_its_stand_in_under_harvest_port(self):
        out = _whole_class(Path("."), self.STAND_IN, "CTextReader", translate(self.IRRLICHT, "daisy"))
        self.assertIn(
            "#ifdef HARVEST_PORT\nclass CTextReader\n{\npublic:\n    char Unrecovered[8];\n};\n#else\nclass CTextReader\n",
            out,
        )
        self.assertIn("    int getText();\n};\n#endif\n}\n", out)

    def test_the_port_build_and_the_census_are_both_checked_for_undefined_symbols(self):
        commands = []

        def run(command, **_):
            commands.append(command[-1])
            return mock.Mock(stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(seed_module.subprocess, "run", run):
            self.assertEqual(port_unresolved(Path(tmp)), {})
        self.assertEqual(commands, ["harvest", "census"])


class ColorPacking(unittest.TestCase):
    def test_a_colour_packing_helper_is_added_and_its_header_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/video").mkdir(parents=True)
            (root / "src/ox/video/ColorPacking.h").write_text("namespace ox {\nnamespace video {\n} // end namespace video\n} // end namespace ox\n")
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/SColor.h").write_text("")
            (root / "src/daisy/video").mkdir(parents=True)
            seed_module._indexes.clear()
            self.assertEqual(_color_helpers(root, "x = ox::video::toA1R5G5B5(c);\n"), ["ox/video/ColorPacking.h"])
            self.assertIn("inline short toA1R5G5B5(unsigned int c)", (root / "src/ox/video/ColorPacking.h").read_text())
            unit = qualify(root, "namespace daisy {\nnamespace video {\nint x = ox::video::toA1R5G5B5(c);\n}\n}\n", "daisy/video/X.cpp")
            self.assertIn('#include "ox/video/ColorPacking.h"', unit)


class GuardedClassReplacement(unittest.TestCase):
    IRRLICHT = "namespace irr {\nnamespace video {\nclass CImage\n{\npublic:\n    int getWidth();\n};\n}\n}\n"
    INSIDE_GUARD = "namespace daisy {\nnamespace video {\nclass CImage\n{\npublic:\n    CImage();\n#ifdef HARVEST_PORT\nprivate:\n    int Data;\n#else\nprivate:\n    char Unrecovered[0x48 - sizeof(int)];\n#endif\n};\n}\n}\n"

    def test_a_class_with_an_ifdef_inside_it_is_replaced_whole(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / IRRLICHT_SOURCE).mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "CImage.h").write_text(self.IRRLICHT)
            (root / "src/daisy/video/Null").mkdir(parents=True)
            header = root / "src/daisy/video/Null/CImage.h"
            header.write_text(self.INSIDE_GUARD)
            seed_module._indexes.clear()
            originals = {}
            self.assertTrue(_declare_members(root, "src/daisy/video/CImage.cpp:3: error: no 'int daisy::video::CImage::getWidth()' member function declared in class 'daisy::video::CImage'\n", originals))
            self.assertIn("int getWidth();", header.read_text())
            self.assertIn("#ifdef HARVEST_PORT\nclass CImage", header.read_text())
            self.assertFalse(_declare_members(root, "src/daisy/video/CImage.cpp:3: error: no 'int daisy::video::CImage::getWidth()' member function declared in class 'daisy::video::CImage'\n", {}))


class ColourFormats(unittest.TestCase):
    def test_an_unqualified_colour_helper_and_the_colour_format_constants_are_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/ox/video").mkdir(parents=True)
            (root / "src/ox/video/ColorPacking.h").write_text("namespace ox {\nnamespace video {\n} // end namespace video\n} // end namespace ox\n")
            (root / "third_party/irrlicht-0.7/include").mkdir(parents=True)
            (root / "third_party/irrlicht-0.7/include/SColor.h").write_text(
                "\tinline s32 A1R5G5B5toA8R8G8B8(s16 color)\n\t{\n\t\treturn color;\n\t}\n"
            )
            seed_module._indexes.clear()
            added = _color_helpers(root, "x = A1R5G5B5toA8R8G8B8(c);\ny = format == ECF_R8G8B8;\n")
            header = (root / "src/ox/video/ColorPacking.h").read_text()
            self.assertEqual(added, ["ox/video/ColorPacking.h"])
            self.assertIn("inline int A1R5G5B5toA8R8G8B8(short color)", header)
            self.assertIn("const ECOLOR_FORMAT ECF_R8G8B8 = (ECOLOR_FORMAT)2;", header)
            self.assertTrue(header.startswith('#include "ox/video/IVideoDriver.h"\n'))


class InventedStructs(unittest.TestCase):
    def test_a_provisional_struct_that_clashes_with_an_adapted_one_is_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/daisy/video/Software").mkdir(parents=True)
            (root / "src/daisy/video/Null").mkdir(parents=True)
            (root / IRRLICHT_SOURCE / "include").mkdir(parents=True)
            (root / "src/daisy/video/Software/S2DVertex.h").write_text(
                "// Adapted from Irrlicht 0.7 include/S2DVertex.h (license: third_party/irrlicht-0.7/include/irrlicht.h).\nnamespace daisy {\nnamespace video {\nstruct S2DVertex\n{\n};\n}\n}\n"
            )
            (root / IRRLICHT_SOURCE / "CVideoNull.h").write_text("namespace irr {\nclass CVideoNull {};\n}\n")
            provisional = root / "src/daisy/video/Null/CVideoNull.h"
            provisional.write_text(
                "namespace daisy {\nnamespace video {\nstruct S2DVertex\n{\n};\nS2DVertex Unused[4];\n}\n}\n"
            )
            unit = root / "src/daisy/video/Software/CVideoSoftware.h"
            unit.write_text('#include "../Null/CVideoNull.h"\n')  # reached through a relative include
            seed_module._indexes.clear()
            renamed = seed_module._rename_invented_structs(root, ["daisy/video/Software/CVideoSoftware.h"])
            self.assertEqual(renamed, ["daisy/video/Null/CVideoNull.h"])
            text = provisional.read_text()
            self.assertIn("struct S2DVertex_", text)
            self.assertIn("S2DVertex_ Unused[4];", text)
            self.assertEqual(seed_module._rename_invented_structs(root, ["daisy/video/Software/CVideoSoftware.h"]), [])


class TopLevelNames(unittest.TestCase):
    def test_a_name_declared_at_the_top_of_ox_is_qualified_and_its_header_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "ox").mkdir(parents=True)
            (root / "src" / "ox" / "Keycodes.h").write_text("namespace ox {\nenum EKEY_CODE\n{\n    KEY_UP = 1\n};\n}\n")
            (root / "src" / "daisy" / "scene").mkdir(parents=True)
            seed_module._indexes.clear()
            text = qualify(root, "namespace daisy {\nnamespace scene {\nEKEY_CODE key = KEY_UP;\n}\n}\n")
            self.assertIn("ox::EKEY_CODE key = ox::KEY_UP;", text)
            self.assertIn("ox::KEY_UP", qualify(root, "namespace daisy {\nnamespace scene {\nint k = daisy::KEY_UP;\n}\n}\n"))
            self.assertIn('#include "ox/Keycodes.h"', text)


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
