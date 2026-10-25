"""Ancestor literal restoration is scoped to one function and target-verified."""

import unittest

from concord.place import candidate_claims, literal_candidate

SYMBOL = '_ZN1A1fEv'


class CandidateClaims(unittest.TestCase):
    def test_existing_claims_keep_evidence_and_new_claims_name_the_object(self):
        rows = [{"symbol": "old", "evidence": "mac:unit"}, {"symbol": "new", "evidence": "reloc:unit:caller"}]
        self.assertEqual(candidate_claims({"old"}, rows, "abc123"), {"new": "candidate-object:abc123:reloc:unit:caller"})
        self.assertEqual(candidate_claims({"old", "new"}, rows, "abc123"), {})


class Literals(unittest.TestCase):
    current = b'int A::f() { log("int file"); return 1; }\nint A::g() { log("int file"); return 2; }\n'
    ancestor = b'int A::f() { log("s32 file"); return 99; }\nint A::g() { log("s32 file"); return 99; }\n'

    def test_only_literals_of_the_named_function_change(self):
        candidate = literal_candidate(self.current, self.ancestor, SYMBOL)
        self.assertEqual(candidate, self.current.replace(b'"int file"', b'"s32 file"', 1))
        self.assertIn(b'return 1;', candidate)
        self.assertIn(b'return 2;', candidate)

    def test_the_inverse_restores_the_original_bytes(self):
        candidate = literal_candidate(self.current, self.ancestor, SYMBOL)
        self.assertEqual(literal_candidate(candidate, self.current, SYMBOL), self.current)

    def test_ambiguous_literal_counts_and_missing_functions_are_skipped(self):
        self.assertIsNone(literal_candidate(self.current, self.ancestor.replace(b'log("s32 file")', b'log("extra", "s32 file")'), SYMBOL))
        self.assertIsNone(literal_candidate(self.current, self.ancestor, '_ZN1A7missingEv'))
        self.assertIsNone(literal_candidate(self.current, self.current, SYMBOL))


@unittest.skipUnless(__import__('shutil').which('as') and __import__('shutil').which('ld'), 'GNU assembler and linker are needed')
class ReadonlyPlacement(unittest.TestCase):
    def test_real_sections_require_unique_complete_unrelocated_data(self):
        import subprocess
        import tempfile
        from pathlib import Path

        from concord.place import readonly_candidates, readonly_sections

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = root / 'data.s'
            obj, target = root / 'data.o', root / 'target'
            constants = ','.join(str(i) for i in range(32))
            source = '.section .rodata,"a"\n.balign 16\n.byte ' + constants + '\n'
            assembly.write_text(source)
            subprocess.run(['as', str(assembly), '-o', str(obj)], check=True)
            subprocess.run(['ld', '-e', '0', str(obj), '-o', str(target)], check=True)
            found = readonly_sections(obj, target, {'.rodata'})
            self.assertEqual(list(found), ['.rodata'])
            self.assertEqual(found['.rodata'] % 16, 0)
            self.assertEqual(readonly_sections(obj, target, set()), {})
            assembly.write_text(source + '.section .other,"a"\n.byte ' + constants + '\n')
            duplicate = root / 'duplicate.o'
            subprocess.run(['as', str(assembly), '-o', str(duplicate)], check=True)
            subprocess.run(['ld', '-e', '0', str(duplicate), '-o', str(target)], check=True)
            self.assertEqual(readonly_sections(obj, target, {'.rodata'}), {})
            for variant in (source.replace('"a"', '"aw"'), source.replace('"a"', '"ax"'), source.replace(constants, '1,2,3')):
                assembly.write_text(variant)
                subprocess.run(['as', str(assembly), '-o', str(obj)], check=True)
                self.assertEqual(readonly_sections(obj, target, {'.rodata'}), {})
            assembly.write_text(source + '.quad external\n.text\n.global external\nexternal: ret\n')
            subprocess.run(['as', str(assembly), '-o', str(obj)], check=True)
            subprocess.run(['ld', '-e', '0', str(obj), '-o', str(target)], check=True)
            self.assertIn('.rodata', readonly_sections(obj, target, {'.rodata'}))
            assembly.write_text(source + '.quad external\n.section .other,"a"\n.balign 16\n.byte ' + constants + '\n.quad external+1\n.text\n.global external\nexternal: ret\n')
            subprocess.run(['as', str(assembly), '-o', str(duplicate)], check=True)
            subprocess.run(['ld', '-e', '0', str(duplicate), '-o', str(target)], check=True)
            self.assertEqual(len(readonly_candidates(obj, target, {'.rodata'})['.rodata']), 2)
            self.assertEqual(readonly_sections(obj, target, {'.rodata'}), {})
            assembly.write_text(source + '.long external@GOTPCREL\n')
            subprocess.run(['as', str(assembly), '-o', str(obj)], check=True)
            self.assertEqual(readonly_sections(obj, target, {'.rodata'}), {})


if __name__ == '__main__':
    unittest.main()
