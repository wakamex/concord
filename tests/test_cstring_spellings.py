"""Compile the authored loop families with C++98 and exercise their operations."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.authored import header_function_candidate
from concord.cstring_spellings import ASSIGN, EQUAL, LESS, POINTER


def fixture():
    # A small string with inline storage isolates the rewritten loops. Allocation
    # and null-input paths lie outside their replacement anchors.
    return b'''template<class T> struct CString
{
    T array[32];
    int used;
    const T* c_str() const { return array; }
    CString() : used(1) { array[0] = 0; }
    CString<T>& operator=(const CString<T>& other)
    {
        used = other.used;
''' + ASSIGN[0].replacements[0].old + b'''
        return *this;
    }
    template<class B> CString<T>& operator=(const B* c)
    {
        int len = 0;
        while (c[len]) ++len;
        used = len + 1;
''' + POINTER[0].replacements[0].old + b'''
        return *this;
    }
    bool operator==(const CString<T>& other) const
    {
''' + EQUAL[0].replacements[0].old + b'''
        return used == other.used;
    }
    bool operator<(const CString<T>& other) const
    {
''' + LESS[0].replacements[0].old + b'''
        return used < other.used;
    }
};
template<class T> int check()
{
    const char* texts[] = {"", "a", "ab", "abc", "z", "za", "\\x80", "a\\xff"};
    for (unsigned i = 0; i < sizeof(texts)/sizeof(*texts); ++i)
        for (unsigned j = 0; j < sizeof(texts)/sizeof(*texts); ++j)
        {
            CString<T> a, b, copy;
            a = texts[i];
            b = texts[j];
            copy = a;
            if (copy.used != a.used) return 1;
            for (int k = 0; k < a.used; ++k)
                if (copy.array[k] != (T)texts[i][k]) return 2;
            bool equal = a.used == b.used;
            for (int k = 0; k < a.used && k < b.used; ++k)
                if (a.array[k] != b.array[k]) equal = false;
            bool less = a.used < b.used;
            for (int k = 0; a.array[k] && b.array[k]; ++k)
                if (a.array[k] != b.array[k])
                {
                    less = a.array[k] < b.array[k];
                    break;
                }
            if ((a == b) != equal || (a < b) != less) return 3;
        }
    return 0;
}
'''


class Spellings(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'needs the C++ compiler')
    def test_every_spelling_compiles_and_preserves_char_and_wide_operations(self):
        source = fixture()
        programs, checks = [], []
        for index, edit in enumerate(ASSIGN + POINTER + EQUAL + LESS):
            with self.subTest(edit=edit.name, declarator=edit.declarator):
                changed = header_function_candidate(source, 'caller', edit)
                self.assertIsNotNone(changed)
                self.assertNotEqual(source, changed)
                self.assertIsNone(header_function_candidate(changed, 'caller', edit))
                namespace = f'form{index}'.encode()
                programs.append(b'namespace ' + namespace + b' {\n' + changed + b'}\n')
                checks.append(b'if (' + namespace + b'::check<char>() || ' + namespace + b'::check<wchar_t>()) return ' + str(index + 1).encode() + b';\n')
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'spellings'
            done = subprocess.run(['g++', '-x', 'c++', '-std=c++98', '-O2', '-', '-o', str(executable)],
                                  input=b''.join(programs) + b'int main() {\n' + b''.join(checks) + b'}\n', capture_output=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr.decode())
            self.assertEqual(subprocess.run([str(executable)], check=False).returncode, 0)


if __name__ == '__main__':
    unittest.main()
