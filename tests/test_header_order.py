"""Splitting a header into movable definitions."""

import unittest

from concord.header_order import _move, definition_blocks

HEADER = b"""#ifndef X_H
#define X_H
namespace ox {
// the first one
inline int A::a() { return 1; }

inline int A::b() { return 2; }
template <class T> inline T A::c(T t) { return t; }
}
#endif
"""


class DefinitionBlocks(unittest.TestCase):
    def test_finds_definitions_inside_guards_and_namespaces(self):
        blocks = definition_blocks(HEADER)
        self.assertEqual(len(blocks.names), 3)
        self.assertEqual(blocks.render((0, 1, 2)), HEADER)

    def test_a_move_carries_comments_and_keeps_everything_else(self):
        blocks = definition_blocks(HEADER)
        moved = blocks.render(_move((0, 1, 2), 0, 2))
        self.assertLess(moved.index(b"A::b()"), moved.index(b"// the first one"))
        self.assertLess(moved.index(b"// the first one"), moved.index(b"A::a()"))
        self.assertEqual(sorted(moved.splitlines()), sorted(HEADER.splitlines()))
        self.assertTrue(moved.startswith(b"#ifndef X_H\n#define X_H\nnamespace ox {\n"))


if __name__ == "__main__":
    unittest.main()
