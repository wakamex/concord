"""The IL parser, writer and legal-edit rules, on the SwitchDown fixture (tests/fixtures/il/switchdown.json, taken
from a capture of tileswitchlogic) and on small hand-made bodies. Nothing here needs a gruntz checkout."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from concord.il import Scope, Statement, Symbols, emit_body, neighbours, parse

FIXTURE = Path(__file__).parent / "fixtures" / "il" / "switchdown.json"

A, B, C, X = b"\x10\x00\x01\x00", b"\x11\x00\x01\x00", b"\x12\x00\x01\x00", b"\x13\x00\x01\x00"  # locals
FN, GET = b"\x20\x00", b"\x21\x00"  # a member function, and a trivial accessor
G = b"\x22\x00\x01\x00"  # a global


def local(handle: bytes, name: str) -> bytes:
    return b"\x01\x01" + handle + b"\x00" + name.encode() + b"\x00"


def gl_record(handle: bytes, mangled: str) -> bytes:
    return handle + b"\x00" + mangled.encode() + b"\x00"


SY = local(A, "a") + local(B, "b") + local(C, "c") + local(X, "x")
GL = gl_record(FN, "?f@C@@QAEHXZ") + gl_record(GET, "?Get@C@@QBEHXZ") + gl_record(G, "?g@@3HA")
SYMBOLS = Symbols(SY, GL, frozenset({"Get"}))


def ref(handle: bytes) -> bytes:
    return b"\x26" + handle


A_ASSIGN_1 = ref(A) + b"\x33\x41\x01" + b"\x32\x41\x4b"  # a = 1;
B_ASSIGN_A = ref(B) + ref(A) + b"\x30\x41" + b"\x32\x41\x4b"  # b = a;
C_ASSIGN_2 = ref(C) + b"\x33\x41\x02" + b"\x32\x41\x4b"  # c = 2;
B_ASSIGN_3 = ref(B) + b"\x33\x41\x03" + b"\x32\x41\x4b"  # b = 3;
X_FROM_A = ref(X) + ref(A) + b"\x30\x41" + b"\x32\x41\x4b"  # x = a;
ASSIGN_A_FROM_B = ref(A) + ref(B) + b"\x30\x41" + b"\x32\x41\x4b"  # a = b;
X_FROM_CALL = ref(X) + ref(FN) + ref(A) + b"\x30\x43" + b"\x99\x43\x3e\x41\x14" + b"\x4c" + b"\x32\x41\x4b"  # x = f(this)
X_FROM_GET = ref(X) + ref(GET) + ref(A) + b"\x30\x43" + b"\x99\x43\x3e\x41\x14" + b"\x4c" + b"\x32\x41\x4b"  # x = Get(this)


def texts(edits) -> list[str]:
    return [edit.text for edit, _ in edits]


def strip_lines(body: bytes) -> bytes:
    return re.sub(rb"\x4f\x01(?:\x80..|.)", b"", body, flags=re.DOTALL)


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        fixture = json.loads(FIXTURE.read_text())
        cls.body = bytes.fromhex(fixture["body"])
        cls.symbols = Symbols(bytes.fromhex(fixture["sy"]), bytes.fromhex(fixture["gl"]), frozenset(fixture["accessors"]))
        cls.items = parse(cls.body, cls.symbols)

    def test_round_trip(self) -> None:
        self.assertEqual(emit_body(self.items), self.body)

    def test_statements_are_decoded(self) -> None:
        self.assertEqual(sum(isinstance(item, Statement) for item in self.items), 9)
        self.assertEqual(sum(isinstance(item, Scope) for item in self.items), 1)  # the block begin in the prologue

    def test_the_known_fix_is_generated_as_two_legal_edits(self) -> None:
        # The two IL edits that make SwitchDown byte-identical to retail (REGISTER-PATHS.md): the use of tileY at line
        # 1013 reads this->m_tileY, and tileY's assignment moves after the layer statement.
        replace = [(e, new) for e, new in neighbours(self.items, self.symbols) if e.kind == "replace" and e.lines == (1013, 1008)]
        self.assertEqual(len(replace), 1)
        after_replace = replace[0][1]
        moves = [e for e, _ in neighbours(after_replace, self.symbols) if e.kind == "move" and "after `layer" in e.text]
        self.assertTrue(any("tileY" in e.text and "(line 1008)" in e.text for e in moves), texts(neighbours(after_replace, self.symbols)))

    def test_the_fix_bytes_match_the_hand_edit(self) -> None:
        # iledit.py's "use1, tileY after layer": the second tileY load replaced by the field read, the assignment moved
        # after the statement ending in 5c (the m_mainPlane member address), with the rest unchanged.
        y_use, y_field = bytes.fromhex("26 36 e4 01 00 30 41"), bytes.fromhex("26 34 e4 01 00 5a a0 43 33 41 0c 27 41 30 41")
        y_stmt = bytes.fromhex("26 36 e4 01 00 26 34 e4 01 00 5a a0 43 33 41 0c 27 41 30 41 32 41 4b")
        layer_end = bytes.fromhex("33 41 5c 27 43 30 43 32 43 4b")
        body = self.body
        uses = []
        at = 0
        while (at := body.find(y_use, at)) >= 0:
            uses.append(at)
            at += len(y_use)
        self.assertEqual(len(uses), 3)
        pieces = body[: uses[1]] + y_field + body[uses[1] + len(y_use) :]
        pieces = pieces.replace(y_stmt, b"", 1)
        anchor = pieces.index(layer_end) + len(layer_end)
        expected = pieces[:anchor] + y_stmt + pieces[anchor:]
        replace = [new for e, new in neighbours(self.items, self.symbols) if e.kind == "replace" and e.lines == (1013, 1008)]
        moved = [new for e, new in neighbours(replace[0], self.symbols) if e.kind == "move" and e.lines == (1008, 1010)]
        # the hand edit leaves its line markers where they were; this move carries each statement's markers with it
        self.assertEqual(strip_lines(emit_body(moved[0])), strip_lines(expected))


class SyntheticTests(unittest.TestCase):
    def test_a_statement_moves_past_one_it_does_not_touch(self) -> None:
        items = parse(A_ASSIGN_1 + C_ASSIGN_2, SYMBOLS)
        moves = [e.text for e, _ in neighbours(items, SYMBOLS) if e.kind == "move"]
        self.assertTrue(any("`c = 2`" in t and "before `a = 1`" in t for t in moves), moves)

    def test_a_statement_does_not_move_past_one_that_writes_what_it_reads(self) -> None:
        items = parse(A_ASSIGN_1 + X_FROM_A, SYMBOLS)  # x = a must stay after a = 1
        moves = [e.text for e, _ in neighbours(items, SYMBOLS) if e.kind == "move"]
        self.assertFalse(any("`x = a`" in t and "before" in t for t in moves), moves)

    def test_a_use_is_replaced_only_when_nothing_between_writes_the_value(self) -> None:
        clean = parse(ASSIGN_A_FROM_B + X_FROM_A, SYMBOLS)  # a = b; x = a;  nothing writes b: the use may take its value
        self.assertTrue(any(e.kind == "replace" for e, _ in neighbours(clean, SYMBOLS)))
        blocked = parse(ASSIGN_A_FROM_B + B_ASSIGN_3 + X_FROM_A, SYMBOLS)  # a = b; b = 3; x = a;  b changes before the use
        replaced = [e.text for e, _ in neighbours(blocked, SYMBOLS) if e.kind == "replace"]
        self.assertFalse(any("with `b`" in t for t in replaced), replaced)

    def test_a_call_of_a_trivial_accessor_moves_but_a_call_of_any_other_function_does_not(self) -> None:
        def moves_past(first: bytes) -> list[str]:
            items = parse(first + C_ASSIGN_2, SYMBOLS)
            return [e.text for e, _ in neighbours(items, SYMBOLS) if e.kind == "move"]

        self.assertTrue(any("before" in t for t in moves_past(X_FROM_GET)))
        self.assertEqual([t for t in moves_past(X_FROM_CALL) if "before" in t], [])

    def test_a_local_whose_address_is_taken_counts_as_memory(self) -> None:
        # Get(this, &x) reads x through its address, so x = 5 cannot move in front of it, though x is a local.
        by_address = ref(GET) + ref(A) + b"\x30\x43" + b"\x99\x43\x3e\x41\x14" + ref(X) + b"\x4c" + b"\x4b"
        x_five = ref(X) + b"\x33\x41\x05" + b"\x32\x41\x4b"
        moves = [e.text for e, _ in neighbours(parse(by_address + x_five, SYMBOLS), SYMBOLS) if e.kind == "move"]
        self.assertFalse(any("`x = 5`" in t and "before" in t for t in moves), moves)

    def test_an_undecoded_byte_is_never_moved_or_edited(self) -> None:
        body = A_ASSIGN_1 + b"\x55" + C_ASSIGN_2
        items = parse(body, SYMBOLS)
        self.assertEqual(emit_body(items), body)
        self.assertEqual(sum(isinstance(item, Statement) for item in items), 2)
        for edit, new in neighbours(items, SYMBOLS):
            self.assertIn(b"\x55", emit_body(new))
            self.assertEqual(emit_body(new).count(b"\x55"), 1, edit.text)

    def test_commutative_operands_swap_only_when_neither_writes(self) -> None:
        pure = ref(X) + ref(A) + b"\x30\x41" + b"\x33\x41\x01" + b"\x02" + b"\x32\x41\x4b"  # x = a + 1;
        swaps = [e.text for e, _ in neighbours(parse(pure, SYMBOLS), SYMBOLS) if e.kind == "swap"]
        self.assertEqual(len(swaps), 1, swaps)

    def test_call_arguments_swap_when_both_are_pure(self) -> None:
        call = ref(X) + ref(FN) + ref(A) + b"\x30\x43" + b"\x99\x43\x3e\x41\x14" + ref(A) + b"\x30\x41" + ref(C) + b"\x30\x41" + b"\x4c" + b"\x32\x41\x4b"  # x = f(this)(a, c)
        swaps = [e.text for e, _ in neighbours(parse(call, SYMBOLS), SYMBOLS) if e.kind == "swap"]
        self.assertEqual(sum("arguments" in t for t in swaps), 1, swaps)


if __name__ == "__main__":
    unittest.main()
