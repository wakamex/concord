"""Destructor context candidates preserve member bodies and compile as C++98."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.destructors import detached, forms


class Destructors(unittest.TestCase):
    def test_implicit_member_keeps_other_members_and_access(self):
        source = b"namespace n { class Base { public: virtual ~Base() {} }; class Child : public Base { private: int x; }; }"
        candidates = forms(source, "Child")
        self.assertIn(b"private: int x;", candidates["explicit"])
        self.assertIn(b"public:\n    ~Child() {}", candidates["explicit"])
        self.assertIn(b"inline Child::~Child() {}", candidates["outlined"])
        self.assertNotIn(b"virtual ~Child", candidates["explicit"])
        self.assertNotIn(b"inline Child", candidates["declared"])

    def test_existing_virtual_body_is_kept(self):
        source = b"namespace n { class A { public: virtual ~A() { release(); } void release() {} }; }"
        candidates = forms(source, "A")
        self.assertIn(b"virtual ~A();", candidates["outlined"])
        self.assertIn(b"inline A::~A() { release(); }", candidates["outlined"])
        self.assertIn(b"void release() {}", candidates["outlined"])
        self.assertEqual(candidates["explicit"], source)
        inverse = forms(candidates["outlined"], "A")["explicit"]
        self.assertIn(b"virtual ~A() { release(); }", inverse)
        self.assertNotIn(b"A::~A", inverse)

    def test_detaching_qualifies_the_existing_namespace(self):
        source = b"namespace ox { namespace scene { class A { public: virtual ~A() { release(); } void release() {} }; } }"
        header, definition = detached(source, "A")
        self.assertIn(b"virtual ~A();", header)
        self.assertEqual(definition, b"inline ox::scene::A::~A() { release(); }")

    def test_unknown_body_or_template_context_is_rejected(self):
        for source in (b"class A { public: virtual ~A(); };", b"template<class T> class A {};", b"class A;"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                forms(source, "A")

    @unittest.skipUnless(shutil.which("g++"), "needs g++")
    def test_generated_forms_compile_with_real_cpp_parser(self):
        source = b"namespace n { class B { public: virtual ~B() {} }; class A : public B { int x; }; }"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.cpp"
            header, definition = detached(source, "A")
            candidates = {**forms(source, "A"), "detached": header + b"\n" + definition}
            for kind, text in candidates.items():
                with self.subTest(kind=kind):
                    path.write_bytes(text + b"\nvoid use() { n::A a; }\n")
                    subprocess.run(["g++", "-std=c++98", "-c", str(path), "-o", str(path.with_suffix(".o"))], check=True, capture_output=True)
