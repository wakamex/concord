"""Concrete types and names for the temporaries a search introduced."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.temporaries import _name_for, rename, variable_types

SOURCE = b"""namespace ns { template <class T> struct Box { Box(const T* p) : p(p) {} const T* p; }; }
struct Item { int Size; };
int use(const ns::Box<char>& b, int n);
int f(const char* text, Item* items, int i)
{
    __typeof__(ns::Box<char>(text)) concordTmp0 = ns::Box<char>(text);
    __typeof__(&items[i]) concordTmp1 = &items[i];
    __typeof__(items[i].Size) concordTmp2 = items[i].Size;
    return use(concordTmp0, concordTmp1->Size + concordTmp2);
}
"""


class Names(unittest.TestCase):
    def test_names_come_from_the_expression(self):
        cases = {
            b"Items[i].Text": "text",
            b"getScreenRes()": "screenRes",
            b"Config->getAttribute(L\"x\")": "attribute",
            b"isVisible()": "visible",
            b"ox::core::CString<char>(extension)": "extensionString",
            b"a + b": "value",
        }
        for expression, name in cases.items():
            self.assertEqual(_name_for(expression), name, expression)

    def test_rename_writes_the_type_and_avoids_taken_names(self):
        source = b"void f() {\n    __typeof__(a.Size) concordTmp0 = a.Size;\n    g(concordTmp0, size);\n}\n"
        self.assertEqual(rename(source, {"concordTmp0": "int"}), b"void f() {\n    int size2 = a.Size;\n    g(size2, size);\n}\n")

    def test_a_conversion_can_be_direct_initialized(self):
        source = b"    __typeof__(ns::Box<char>(text)) concordTmp0 = ns::Box<char>(text);\n    use(concordTmp0);\n"
        types = {"concordTmp0": "ns::Box<char>"}
        self.assertIn(b"ns::Box<char> textBox(text);", rename(source, types, direct=True))
        self.assertIn(b"ns::Box<char> textBox = ns::Box<char>(text);", rename(source, types, direct=False))


@unittest.skipUnless(shutil.which("g++"), "needs g++")
class DwarfTypes(unittest.TestCase):
    def test_types_are_read_from_the_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "f.cpp").write_bytes(SOURCE)
            obj = Path(tmp) / "f.o"
            subprocess.run(["g++", "-O2", "-g", "-c", "f.cpp", "-o", str(obj)], cwd=tmp, check=True)
            self.assertEqual(
                variable_types(obj),
                {"concordTmp0": "ns::Box<char>", "concordTmp1": "Item*", "concordTmp2": "int"},
            )


if __name__ == "__main__":
    unittest.main()
