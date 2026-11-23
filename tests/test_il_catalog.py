"""The IL decoding catalog: each construct the parser decodes, compiled from a snippet in tests/fixtures/il/catalog with
the project's compiler, with the decoding its statements have (tests/fixtures/il/catalog.json).

Every function of every snippet is captured through concord's gruntz checkout and parsed against the capture's
symbol tables, which the fixture stores, so the decoding tests need no checkout. The checkout test recompiles each
snippet and checks its bodies and their decodings against the fixture, so a change in the compiler's output or in
the parser shows.
Regenerate the fixture after a deliberate change with:

    CONCORD_GRUNTZ=ROOT uv run --no-config python -m tests.test_il_catalog --write
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from concord.il import Statement, Symbols, emit_body, parse, pretty

HERE = Path(__file__).resolve().parent
SNIPPETS = HERE / "fixtures" / "il" / "catalog"
FIXTURE = HERE / "fixtures" / "il" / "catalog.json"
UNIT = "grunt"  # cpp-rtti flags, the common profile of gruntz-decomp's C++ units
END = b"\x4b"


def checkout() -> Path | None:
    """The gruntz-decomp checkout named by CONCORD_GRUNTZ, after `gruntz build`, or None."""
    root = os.environ.get("CONCORD_GRUNTZ")
    if not root:
        return None
    path = Path(root).resolve()
    return path if (path / "build/objdiff/compare-new/report.json").is_file() else None


def statement_text(statement: Statement) -> str:
    """A statement as the decoding tests compare it: its expression, and the byte that ends it unless that is 4b."""
    text = pretty(statement.root)
    return text if statement.tail == END else f"{text} ; ends {statement.tail.hex(' ')}"


def describe(body: bytes, symbols: Symbols) -> dict:
    items = parse(body, symbols)
    if emit_body(items) != body:
        raise AssertionError("the parse does not give back the body's bytes")
    return {
        "statements": [statement_text(item) for item in items if isinstance(item, Statement)],
        "opaque_bytes": sum(len(item) for item in items if type(item) is bytes),
    }


def compile_snippets(root: Path) -> dict:
    """Capture every snippet through the checkout and return the fixture's contents for them."""
    from concord.gruntz import Gruntz
    from concord.il_search import Capture, trivial_accessors

    gruntz = Gruntz(root)
    accessors = trivial_accessors(root)
    sources = {}
    for path in sorted(SNIPPETS.glob("*.cpp")):
        il, evaluation = gruntz.capture_il(UNIT, path.read_bytes(), score=False)
        if il is None:
            raise RuntimeError(f"{path.name} did not compile: {evaluation.error[-800:]}")
        capture = Capture(il)
        ex = capture.read("ex")
        symbols = capture.symbols(accessors)
        functions = {}
        for name, start, end in capture.bodies():
            body = ex[start:end]
            functions[name] = {"body": body.hex(" "), **describe(body, symbols)}
        sources[path.name] = {"sy": capture.read("sy").hex(" "), "gl": capture.read("gl").hex(" "), "functions": functions}
    flags = gruntz.configured_flags(UNIT)
    return {"unit": UNIT, "flags": flags, "sources": sources}


# Functions whose bodies keep opaque bytes, with the reason. The vtable record (5c T H 4b) is not decoded: the captures
# show three shapes for its operand, none of which fixes the operand's width.
KNOWN_GAPS = {"?c_ctor@@YAXXZ": "the vtable record 5c"}

# Constructs the catalog must show, each as text a decoded statement contains: a member call, a conditional, an index,
# a compound assignment, a branch on true and on false, a return store, a switch dispatch and a case.
CONSTRUCTS = {
    "a member call": "->",
    "a conditional": " ? ",
    "an index": "[",
    "a compound assignment": " += ",
    "a branch when true": "; ends 39 ",
    "a branch when false": "; ends 38 ",
    "a return store": "; ends 3a ",
    "a switch dispatch": "; ends 3b ",
    "a switch case": "; ends 3d ",
}


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE.read_text())

    def test_each_snippet_function_decodes_as_recorded(self) -> None:
        for name, source in self.fixture["sources"].items():
            symbols = Symbols(bytes.fromhex(source["sy"]), bytes.fromhex(source["gl"]))
            for function, record in source["functions"].items():
                with self.subTest(source=name, function=function):
                    described = describe(bytes.fromhex(record["body"]), symbols)  # raises unless the bytes round trip
                    self.assertEqual(described, {"statements": record["statements"], "opaque_bytes": record["opaque_bytes"]})

    def test_only_the_known_gaps_keep_opaque_bytes(self) -> None:
        opaque = {function for source in self.fixture["sources"].values() for function, record in source["functions"].items() if record["opaque_bytes"]}
        self.assertEqual(opaque, set(KNOWN_GAPS))

    def test_the_catalog_shows_each_construct(self) -> None:
        texts = [text for source in self.fixture["sources"].values() for record in source["functions"].values() for text in record["statements"]]
        for construct, marker in CONSTRUCTS.items():
            with self.subTest(construct=construct):
                self.assertTrue(any(marker in text for text in texts), marker)


@unittest.skipUnless(checkout(), "needs a gruntz-decomp checkout after `gruntz build` (CONCORD_GRUNTZ)")
class CheckoutTests(unittest.TestCase):
    def test_the_compiler_still_gives_the_fixture_bodies(self) -> None:
        # The bodies must match exactly, and each body must decode to the recorded statements under the fresh symbol
        # tables. The tables themselves are not compared: their records name the temporary source file, with its
        # process id, that the capture was compiled from.
        fresh = compile_snippets(checkout())
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(fresh["flags"], fixture["flags"])
        for name, source in fixture["sources"].items():
            with self.subTest(source=name):
                fresh_source = fresh["sources"][name]
                self.assertEqual({k: v["body"] for k, v in fresh_source["functions"].items()},
                                 {k: v["body"] for k, v in source["functions"].items()})
                symbols = Symbols(bytes.fromhex(fresh_source["sy"]), bytes.fromhex(fresh_source["gl"]))
                for function, record in source["functions"].items():
                    with self.subTest(function=function):
                        described = describe(bytes.fromhex(record["body"]), symbols)
                        self.assertEqual(described, {"statements": record["statements"], "opaque_bytes": record["opaque_bytes"]})


def main(argv: list[str]) -> int:
    if argv != ["--write"]:
        print(__doc__)
        return 2
    root = checkout()
    if root is None:
        print("set CONCORD_GRUNTZ to a gruntz-decomp checkout after `gruntz build`", file=sys.stderr)
        return 2
    FIXTURE.write_text(json.dumps(compile_snippets(root), indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
