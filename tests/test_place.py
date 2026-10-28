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
class BssPlacement(unittest.TestCase):
    def test_sweep_places_only_unplaced_bss_proved_by_the_static_initializer(self):
        import json
        import subprocess
        import tempfile
        from pathlib import Path

        from concord.harvest import BUILD, Harvest
        from concord.place import place_bss

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / 'build' / 'match' / BUILD
            reports.mkdir(parents=True)
            target = root / 'orig' / BUILD / 'Harvest'
            target.parent.mkdir(parents=True)
            source = root / 'target.s'
            source.write_text('''
.text
.global anchor_good
.type anchor_good,@function
anchor_good:
    ret
.size anchor_good, .-anchor_good
.balign 16
.global _GLOBAL__I_good
.type _GLOBAL__I_good,@function
_GLOBAL__I_good:
    mov $0x2000, %edi
    ret
.size _GLOBAL__I_good, .-_GLOBAL__I_good
.balign 16
.global anchor_bad
.type anchor_bad,@function
anchor_bad:
    ret
.size anchor_bad, .-anchor_bad
.balign 16
.global _GLOBAL__I_bad
.type _GLOBAL__I_bad,@function
_GLOBAL__I_bad:
    mov $0x3000, %edi
    ret
.size _GLOBAL__I_bad, .-_GLOBAL__I_bad
.balign 16
.global _GLOBAL__I_unanchored
.type _GLOBAL__I_unanchored,@function
_GLOBAL__I_unanchored:
    mov $0x2000, %edi
    ret
.size _GLOBAL__I_unanchored, .-_GLOBAL__I_unanchored
.section .bss
.balign 4
.space 4
''')
            target_object = root / 'target.o'
            subprocess.run(['as', str(source), '-o', str(target_object)], check=True)
            subprocess.run(['ld', '-e', '_GLOBAL__I_good', '--section-start=.text=0x1000',
                            '--section-start=.bss=0x2000', str(target_object), '-o', str(target)], check=True)

            units = ['daisy/Good.cpp', 'daisy/Bad.cpp', 'daisy/Unanchored.cpp']
            (root / 'config' / BUILD).mkdir(parents=True)
            (root / 'config' / BUILD / 'units.toml').write_text(
                ''.join(f'["{unit}"]\n".text" = {address:#x}\n\n'
                        for unit, address in zip(units, (0x1000, 0x1020, 0x1040), strict=True))
            )
            for unit, name, immediate, address in zip(
                units[:2], ('good', 'bad'), (0x2000, 0x3000), (0x1000, 0x1020), strict=True
            ):
                asm = root / f'{name}.s'
                asm.write_text(f'''
.text
.global anchor_{name}
.type anchor_{name},@function
anchor_{name}:
    ret
.size anchor_{name}, .-anchor_{name}
.balign 16
.global _GLOBAL__I_{name}
.type _GLOBAL__I_{name},@function
_GLOBAL__I_{name}:
    mov ${immediate:#x}, %edi
    ret
.size _GLOBAL__I_{name}, .-_GLOBAL__I_{name}
.section .bss
.space 4
''')
                obj = reports / f'{unit.replace("/", "__").removesuffix(".cpp")}.o'
                subprocess.run(['as', str(asm), '-o', str(obj)], check=True)
                report = {
                    'unit': unit,
                    'unplaced_sections': ['.bss'],
                    'sections': [{
                        'name': '.text',
                        'address': f'{address:#x}',
                        'placement': f'symbol: anchor_{name}',
                        'functions': [{'symbol': f'anchor_{name}', 'address': f'{address:#x}', 'exact': True}],
                    }],
                }
                (obj.with_suffix('.json')).write_text(json.dumps(report))
            unanchored_asm = root / 'unanchored.s'
            unanchored_asm.write_text('''
.text
.global _GLOBAL__I_unanchored
.type _GLOBAL__I_unanchored,@function
_GLOBAL__I_unanchored:
    mov $0x2000, %edi
    ret
.size _GLOBAL__I_unanchored, .-_GLOBAL__I_unanchored
.section .bss
.space 4
''')
            unanchored_obj = reports / 'daisy__Unanchored.o'
            subprocess.run(['as', str(unanchored_asm), '-o', str(unanchored_obj)], check=True)
            (unanchored_obj.with_suffix('.json')).write_text(json.dumps({
                'unit': units[2],
                'unplaced_sections': ['.bss'],
                'sections': [{'name': '.text', 'address': '0x1040'}],
            }))

            placements = place_bss(Harvest(root))

            self.assertEqual(placements, [{'unit': units[0], 'address': 0x2000}])
            config = (root / 'config' / BUILD / 'units.toml').read_text()
            self.assertIn('["daisy/Good.cpp"]\n".text" = 0x1000\n".bss" = 0x2000', config)
            self.assertIn('["daisy/Bad.cpp"]\n".text" = 0x1020', config)
            self.assertNotIn('["daisy/Bad.cpp"]\n".text" = 0x1020\n".bss"', config)
            self.assertIn('["daisy/Unanchored.cpp"]\n".text" = 0x1040', config)
            self.assertNotIn('["daisy/Unanchored.cpp"]\n".text" = 0x1040\n".bss"', config)


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
