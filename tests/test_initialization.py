"""Initialization arguments preserve scalar and pointer vector contents."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from concord.initialization import zero_resize


class Initialization(unittest.TestCase):
    def test_only_the_named_vector_call_changes_and_class_types_are_rejected(self):
        source = b'void A::f(int n) { Items.resize(n); other.resize(n); for(int i=0;i<n;++i) Items[i]=0; }\n'
        forms = zero_resize(source, 'A::f', 'Items', 'Node*')
        self.assertEqual(forms['default'], source)
        self.assertIn(b'Items.resize(n, 0)', forms['zero'])
        self.assertIn(b'Items.resize(n, (Node*)0)', forms['typed-zero'])
        self.assertTrue(all(b'other.resize(n);' in text for text in forms.values()))
        with self.assertRaisesRegex(ValueError, 'scalar or object-pointer'):
            zero_resize(source, 'A::f', 'Items', 'Widget')

    @unittest.skipUnless(shutil.which('g++'), 'g++ is needed')
    def test_real_std_vector_forms_keep_existing_elements_and_initialize_new_ones(self):
        source = b'''#include <vector>
class A { public: std::vector<int*> Items; void f(int n); };
void A::f(int n) { Items.resize(n); }
int main() { int value=1; A a; a.Items.push_back(&value); a.f(3); return a.Items[0]!=&value || a.Items[1]!=0 || a.Items[2]!=0; }
'''
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in zero_resize(source, 'A::f', 'Items', 'int*').items():
                path = Path(tmp)/f'{name}.cpp'
                path.write_bytes(text)
                binary = path.with_suffix('')
                subprocess.run(['g++', '-std=c++98', '-O2', str(path), '-o', str(binary)], check=True, capture_output=True)
                self.assertEqual(subprocess.run([str(binary)], check=False).returncode, 0)
