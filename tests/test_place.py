"""Ancestor literal restoration is scoped to one function and target-verified."""

import unittest

from concord.place import literal_candidate

SYMBOL = '_ZN1A1fEv'


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


if __name__ == '__main__':
    unittest.main()
