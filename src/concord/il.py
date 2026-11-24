"""MSVC 5.0's intermediate language (IL) for one function: a parser that splits a function's `ex` body into
statements and expression trees, a writer that turns them back into the same bytes, and the effect analysis
that decides which edits to them are legal.

A body is postfix bytecode: each operand comes before the operator that consumes it. The opcodes are pinned by
the snippets in tests/fixtures/il/catalog.json, each compiled with the project's compiler and checked by
tests/test_il_catalog.py. The operands, with T a type (one byte, or two when the first has its high bit set):

    26 H, 2d H, 5e H    a symbol, a parameter, a symbol's value; H is 2 bytes, or 4 when its bytes 2 and 3 are 01 00
    9b T H              a symbol's address, typed T
    33 T V              constant V: a byte below 0x80, or 80 and four bytes; 80 V (four bytes) with no type
    30 T, 5a T, 41 T    load, this, conversion to T
    27 T                member address: base pointer, then a constant offset
    77 T, 28 00 00      element: base, then index (typed; or untyped with the element size folded in)
    02 03 04 05 06      add, subtract, multiply, divide, modulo
    09 0a 0b 0c 0d      shift left, shift right, and, or, exclusive or
    1b 1c               logical or, logical and (as values)
    1f 20 21 22 23 24   equal, not equal, less or equal, less, greater or equal, greater
    44                  comma: the left operand is evaluated and dropped
    43 T                conditional: the condition, then the two arms; T is the result type
    08 0e 1a            negate, bitwise not, logical not
    32 T                assign: left, then right
    0f 10 11 15 16 17 18 19 35 36 T
                        compound assignment (add, subtract, multiply, shift left, shift right, and, exclusive
                        or, or) and post-increment or post-decrement: left, then right
    2c T 00             conversion to T
    55 T                the end of a call argument, typed T
    99 T 3e U 14, 9a T 3e U 14, 99 T 46
                        member call: callee and this are pushed, then the arguments, then 4c; U is the return type,
                        or no return type is written (46)
    3e U 00             free or indirect call: the callee is pushed, then the arguments, then 4c
    4c                  call close; with nothing pending it ends a switch's case table
    A call may end a statement with no 4b: when a complete call is followed by a marker, a line marker, a symbol, a
    constant or an undecoded byte, the call is a statement of its own.
    38 H, 39 H          branch to label H when the condition is false, or when it is true; the statement is the condition
    3a H                store to H after a value (a return), or goto H with nothing on the stack
    47                  return; after a value with no store (a constructor returns this), it ends that statement
    3b H, 3c T H        switch dispatch on the value; each 3d H ends a case: its constant is the case value
    29 H                label H
    53, 54 N            block begin, block end N
    4f 01 L             line marker, L one byte or 80 followed by two bytes
    26 H 46 2d P.. 4c   a function's declaration: its own symbol, then each parameter
    4f 1f .., 4f 20 08, 4f 02 H, 4f 23 01 0d V.., 2a H .., 2b H 01, 4d
                        function header, frame records, method this records and end of file

Statements are the only items edits may change. Block markers (53, 54) and line markers are transparent to
values, so an edit may move a value across them, but no statement moves across a marker. Labels, gotos,
branches, switches, returns, declarations and headers are markers or statement ends: no value and no statement
crosses them. Any byte the parser does not decode is opaque and no edit touches it; it ends the statement it sits
in. The vtable record (5c) is not decoded, and a symbol whose handle the sy and gl tables do not name is treated as
memory: both only remove edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

END = 0x4B
CALL_CLOSE = 0x4C
LOCAL = re.compile(rb"\x01.(....|..)\x00([A-Za-z_][\x21-\x7e]*)\x00", re.DOTALL)  # sy: 01 BB HANDLE 00 name 00, HANDLE 4 or 2 bytes
GLOBAL = re.compile(rb"([?][\x21-\x7e]{3,})\x00")  # gl: handle bytes 00 ?name 00
SCOPE = re.compile(rb"(?:\x4f\x01(?:\x80..|.)|\x53|\x54.)*", re.DOTALL)  # block begins, block ends and line markers only

BINARY = {  # operators with no type byte, by opcode
    0x02: "add", 0x03: "sub", 0x04: "mul", 0x05: "div", 0x06: "mod", 0x09: "shl", 0x0A: "shr", 0x0B: "and",
    0x0C: "or", 0x0D: "xor", 0x1B: "lor", 0x1C: "land", 0x1F: "eq", 0x20: "ne", 0x21: "le", 0x22: "lt",
    0x23: "ge", 0x24: "gt", 0x44: "comma",
}
UNARY = {0x08: "neg", 0x0E: "inv", 0x1A: "lnot"}
COMPOUND = {0x0F: "addeq", 0x10: "subeq", 0x11: "muleq", 0x15: "shleq", 0x16: "shreq", 0x17: "andeq", 0x18: "xoreq",
            0x19: "oreq", 0x35: "inc", 0x36: "dec"}  # each takes a type byte: lhs, rhs, op T
SYMBOL = {
    "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%", "shl": "<<", "shr": ">>", "and": "&", "or": "|",
    "xor": "^", "lor": "||", "land": "&&", "eq": "==", "ne": "!=", "le": "<=", "lt": "<", "ge": ">=", "gt": ">",
    "comma": ",", "off": "+", "idx": "[]", "index": "[]", "assign": "=", "addeq": "+=", "subeq": "-=", "muleq": "*=",
    "shleq": "<<=", "shreq": ">>=", "andeq": "&=", "xoreq": "^=", "oreq": "|=", "inc": "+=", "dec": "-=",
}
PREFIX = {"neg": "-", "inv": "~", "lnot": "!"}
COMPOUND_NAMES = frozenset(COMPOUND.values())
ASSIGNS = frozenset({"assign"}) | COMPOUND_NAMES  # the writes: the left operand is written, and a compound also read
STATEMENT_ENDS = frozenset({"end", "branch", "branch_true", "store", "switch", "case"})  # 47 also ends a value (see parse)  # tokens that end a statement with a value or a condition
BOUNDARY = frozenset({"block", "label", "ret", "eof", "header", "frame", "decl", "method", "goto"})  # not in a statement
IMPLICIT_ENDS = BOUNDARY | {"block", "line", "sym", "const", "bad"}  # a complete call before one of these ends its statement


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "local", "data", "function", or "unknown" for a handle the tables do not name
    handle: bytes  # the bytes the IL writes after opcode 26

    @property
    def short(self) -> str:
        """The unqualified name: a mangled global's or function's name without its scope and signature."""
        return self.name[1 : self.name.find("@")] if self.name.startswith("?") and "@" in self.name else self.name


class Symbols:
    """The capture's names by the handle bytes the IL writes after opcode 26. Locals come from `sy`; globals and
    functions from `gl`, where each record is its handle (2 bytes, or 4 when they end in 01 00), 00, then the
    mangled name. `accessors` holds the names of trivial accessors: a call of one only reads memory."""

    def __init__(self, sy: bytes, gl: bytes, accessors: frozenset[str] = frozenset()) -> None:
        self.accessors = accessors
        self._by_handle: dict[bytes, Symbol] = {}
        for match in LOCAL.finditer(sy):
            handle = bytes(match[1])
            self._by_handle.setdefault(handle, Symbol(match[2].decode("latin-1"), "local", handle))
        for match in GLOBAL.finditer(gl):
            p = match.start()
            if p < 3 or gl[p - 1] != 0:
                continue
            four = gl[p - 5 : p - 1] if p >= 5 else b""
            handle = four if len(four) == 4 and four[2:] == b"\x01\x00" else gl[p - 3 : p - 1]
            name = match[1].decode("latin-1")
            code = name[name.find("@@") + 2 : name.find("@@") + 3] if "@@" in name else ""
            kind = "data" if code == "3" or "@@" not in name else "function"
            self._by_handle.setdefault(handle, Symbol(name, kind, handle))

    def lookup(self, handle: bytes) -> Symbol | None:
        return self._by_handle.get(handle)

    def is_accessor(self, symbol: Symbol) -> bool:
        """Whether a function symbol is a trivial accessor, by the unqualified name of its mangled form."""
        return symbol.kind == "function" and symbol.short in self.accessors


@dataclass(frozen=True)
class Token:
    kind: str  # a Node kind, or one of: sym, const, callopen, callclose, store, branch, label, end, line, block, ...
    raw: bytes
    symbol: Symbol | None = None
    value: int | None = None  # a constant's value, a line number, or a call opening's fixed operands (callee, and this)


@dataclass(frozen=True)
class Node:
    """An operator or leaf. Operands come before `raw` in the bytes. A call's fixed operands (its callee, and this
    for a member call) come first, its arguments after `raw`, which holds the opening bytes; the call closes with 4c."""

    raw: bytes
    kind: str  # sym, const, load, this, off, conv, argsep, neg, inv, lnot, a BINARY name, assign, opassign, idx, call
    children: tuple[Node, ...] = ()
    symbol: Symbol | None = None
    value: int | None = None
    fixed: int = 0  # for a call: how many leading children are its callee and this

    def emit(self) -> bytes:
        if self.kind == "call":
            head = b"".join(child.emit() for child in self.children[: self.fixed])
            return head + self.raw + b"".join(a.emit() for a in self.children[self.fixed :]) + bytes([CALL_CLOSE])
        return b"".join(child.emit() for child in self.children) + self.raw


@dataclass(frozen=True)
class Statement:
    prefix: bytes  # line markers before the statement
    line: int | None
    root: Node
    tail: bytes = bytes([END])  # 4b, or the store or branch that ends the statement

    def emit(self) -> bytes:
        return self.prefix + self.root.emit() + self.tail


class Scope(bytes):
    """Opaque bytes made only of block markers and line markers: no value changes across them, but no statement
    moves across them, since a statement stays in its block."""


class Marker(bytes):
    """Decoded bytes that change control flow or the frame: a label, goto, return, declaration or header. No value
    and no statement crosses them."""


Item = Statement | bytes  # other bytes are opaque: the parser did not decode them, and no edit touches them


def emit_body(items: tuple[Item, ...]) -> bytes:
    return b"".join(item if isinstance(item, bytes) else item.emit() for item in items)


def _handle_width(body: bytes, k: int) -> int:
    """The width of a symbol handle starting at k: four bytes when bytes k+2 and k+3 are 01 00, otherwise two."""
    return 4 if body[k + 2 : k + 4] == b"\x01\x00" else 2


def _type_end(body: bytes, k: int) -> int | None:
    """The end of a type field at k: one byte, or two when the first has its high bit set (a0 43 is a pointer type
    with the flag, 80 85 is double)."""
    if k >= len(body):
        return None
    return k + 2 if body[k] & 0x80 and k + 1 < len(body) else k + 1


def read_token(body: bytes, i: int, symbols: Symbols) -> Token:
    """The token at i. A token of kind 'bad' is one byte the parser does not decode."""
    n = len(body)
    c = body[i]
    bad = Token("bad", body[i : i + 1])

    def at(k: int) -> int | None:
        return body[k] if k < n else None

    if c in (0x26, 0x2D):  # symbol reference or parameter
        if i + 2 >= n:
            return bad
        end = i + 1 + _handle_width(body, i + 1)
        if end > n:
            return bad
        handle = body[i + 1 : end]
        if c == 0x26 and at(end) == 0x46:  # a declaration: 46, each parameter as 2d, then 4c
            k = end + 1
            while at(k) == 0x2D:
                k += 1 + _handle_width(body, k + 1)
            if at(k) == CALL_CLOSE:
                return Token("decl", body[i : k + 1])
        return Token("sym", body[i:end], symbols.lookup(handle) or Symbol(f"?{handle.hex()}", "unknown", handle))
    if c == 0x33:  # constant
        k = _type_end(body, i + 1)
        if k is None or at(k) is None:
            return bad
        value = body[k]
        if value == 0x80 and k + 5 <= n:
            return Token("const", body[i : k + 5], value=int.from_bytes(body[k + 1 : k + 5], "little", signed=True))
        if value < 0x80:
            return Token("const", body[i : k + 1], value=value)
        return bad
    if c in (0x30, 0x5A, 0x27, 0x41, 0x32, 0x55, 0x77):  # load, this, member address, conversion, assign, argument end, index
        k = _type_end(body, i + 1)
        if k is None:
            return bad
        kind = {0x30: "load", 0x5A: "this", 0x27: "off", 0x41: "conv", 0x32: "assign", 0x55: "argsep", 0x77: "index"}[c]
        return Token(kind, body[i:k])
    if c == 0x2C:  # conversion, with a 00 after the type
        k = _type_end(body, i + 1)
        if k is None or at(k) != 0x00:
            return bad
        return Token("conv", body[i : k + 1])
    if c in COMPOUND:
        k = _type_end(body, i + 1)
        if k is None:
            return bad
        return Token(COMPOUND[c], body[i:k])
    if c in BINARY:
        return Token(BINARY[c], body[i : i + 1])
    if c in UNARY:
        return Token(UNARY[c], body[i : i + 1])
    if c == 0x3B or c == 0x3C or c == 0x3D:  # switch dispatch (3b H, 3c T H), and a case's label (3d H): each ends a statement
        k = i + 1
        if c == 0x3C:
            k = _type_end(body, k)
            if k is None:
                return bad
        end = k + _handle_width(body, k)
        if end > n:
            return bad
        return Token("case" if c == 0x3D else "switch", body[i:end])
    if c == 0x43:  # conditional: the result type follows
        k = _type_end(body, i + 1)
        return Token("select", body[i:k]) if k is not None else bad
    if c == 0x28 and body[i : i + 3] == b"\x28\x00\x00":
        return Token("idx", body[i : i + 3])
    if c == END:
        return Token("end", body[i : i + 1])
    if c == CALL_CLOSE:
        return Token("callclose", body[i : i + 1])
    if c in (0x3A, 0x38, 0x39, 0x29):  # store or goto, branch when false or true, label: each names a label
        end = i + 1 + _handle_width(body, i + 1)
        if end > n:
            return bad
        kind = {0x3A: "store", 0x38: "branch", 0x39: "branch_true", 0x29: "label"}[c]
        return Token(kind, body[i:end], symbols.lookup(body[i + 1 : end]))
    if c == 0x99 or c == 0x9A:  # member call: this's type, then 46, or 3e U 14 with the return type U
        k = _type_end(body, i + 1)
        if k is None:
            return bad
        if at(k) == 0x46:
            return Token("callopen", body[i : k + 1], value=2)
        if at(k) == 0x3E:
            k2 = _type_end(body, k + 1)
            if k2 is not None and at(k2) == 0x14:
                return Token("callopen", body[i : k2 + 1], value=2)
        return bad
    if c == 0x5E:  # a symbol's value, written with no type
        end = i + 1 + _handle_width(body, i + 1)
        if end > n:
            return bad
        return Token("sym", body[i:end], symbols.lookup(body[i + 1 : end]) or Symbol(f"?{body[i + 1 : end].hex()}", "unknown", body[i + 1 : end]))
    if c == 0x9B:  # a symbol's address with its type
        k = _type_end(body, i + 1)
        if k is None or k + 2 > n:
            return bad
        end = k + _handle_width(body, k)
        if end > n:
            return bad
        return Token("sym", body[i:end], symbols.lookup(body[k:end]) or Symbol(f"?{body[k:end].hex()}", "unknown", body[k:end]))
    if c == 0x80 and i + 5 <= n:  # a wide constant whose type the context gives
        return Token("const", body[i : i + 5], value=int.from_bytes(body[i + 1 : i + 5], "little", signed=True))
    if c == 0x3E:  # free call: the return type, then 00
        k = _type_end(body, i + 1)
        if k is not None and at(k) == 0x00:
            return Token("callopen", body[i : k + 1], value=1)
        return bad
    if c == 0x53:
        return Token("block", body[i : i + 1])
    if c == 0x54 and i + 1 < n:
        return Token("block", body[i : i + 2])
    if c == 0x47:
        return Token("ret", body[i : i + 1])
    if c == 0x4D:
        return Token("eof", body[i : i + 1])
    if c == 0x2A:  # method this record: 2a H a0 43 00 01
        end = i + 1 + _handle_width(body, i + 1)
        if body[end : end + 4] == b"\xa0\x43\x00\x01":
            return Token("method", body[i : end + 4])
        return bad
    if c == 0x2B:  # 2b H 01
        end = i + 1 + _handle_width(body, i + 1)
        if at(end) == 0x01:
            return Token("method", body[i : end + 1])
        return bad
    if c == 0x4F and i + 1 < n:
        sub = body[i + 1]
        if sub == 0x01 and at(i + 2) is not None:  # line marker
            m = 5 if body[i + 2] == 0x80 else 3
            if i + m <= n:
                value = int.from_bytes(body[i + 3 : i + 5], "little") if m == 5 else body[i + 2]
                return Token("line", body[i : i + m], value=value)
            return bad
        if sub == 0x1F and body[i + 2 : i + 7] == b"\x80\x00\x08\xa0\x00":  # function start
            return Token("header", body[i : i + 7])
        if sub == 0x20 and at(i + 2) == 0x08:
            return Token("header", body[i : i + 3])
        if sub == 0x02 and i + 2 < n:  # the file's symbol
            end = i + 2 + _handle_width(body, i + 2)
            return Token("header", body[i:end]) if end <= n else bad
        if sub == 0x23 and at(i + 2) == 0x01:  # frame: 0d V for each slot, V one byte, or 80 and two
            k = i + 3
            while at(k) == 0x0D:
                k += 4 if at(k + 1) == 0x80 else 2
            return Token("frame", body[i:k])
    return bad


def _build(tokens: list[Token], tail: bytes) -> Statement | None:
    """The statement that a run of tokens makes when `tail` ends it, or None when the run is not one whole expression."""
    prefix = bytearray()
    line = None
    while tokens and tokens[0].kind == "line":
        prefix += tokens[0].raw
        line = tokens[0].value
        tokens = tokens[1:]
    stack: list[Node] = []
    frames: list[tuple[int, bytes, int]] = []  # per open call: the stack height at its opening (after its fixed operands), its bytes, and how many fixed operands
    for t in tokens:
        floor = frames[-1][0] if frames else 0  # an operator may not reach below the innermost open call
        kind = t.kind
        if kind == "sym":
            stack.append(Node(t.raw, "sym", (), t.symbol))
        elif kind == "const":
            stack.append(Node(t.raw, "const", (), None, t.value))
        elif kind in ("load", "this", "conv", "argsep", "neg", "inv", "lnot"):
            if len(stack) <= floor:
                return None
            stack.append(Node(t.raw, kind, (stack.pop(),)))
        elif kind == "select":
            if len(stack) < floor + 3:
                return None
            other, then, cond = stack.pop(), stack.pop(), stack.pop()
            stack.append(Node(t.raw, "select", (cond, then, other)))
        elif kind in ("off", "idx", "index", "assign") or kind in BINARY.values() or kind in COMPOUND_NAMES:
            if len(stack) < floor + 2:
                return None
            right, left = stack.pop(), stack.pop()
            stack.append(Node(t.raw, kind, (left, right)))
        elif kind == "callopen":
            if len(stack) < floor + t.value:
                return None
            frames.append((len(stack), t.raw, t.value))
        elif kind == "callclose":
            if not frames:
                return None
            height, raw, fixed = frames.pop()
            if height - fixed < 0 or len(stack) < height:
                return None
            fixed_nodes = tuple(stack[height - fixed : height])
            args = tuple(stack[height:])
            del stack[height - fixed :]
            stack.append(Node(raw, "call", (*fixed_nodes, *args), fixed=fixed))
        else:  # a line marker inside an expression, or an undecoded token
            return None
    if frames or len(stack) != 1:
        return None
    return Statement(bytes(prefix), line, stack[0], tail)


def _classify(chunk: bytes) -> type:
    """The class of undecoded-or-marked bytes: transparent block and line markers, or opaque bytes."""
    return Scope if SCOPE.fullmatch(chunk) else bytes


def parse(body: bytes, symbols: Symbols) -> tuple[Item, ...]:
    """A function body as items: statements, markers, and opaque bytes. emit_body gives back `body`. An
    undecoded byte ends the statement it sits in, so one unknown opcode makes only its own statement opaque."""
    items: list[Item] = []

    def put(chunk: bytes, cls: type) -> None:
        if not chunk:
            return
        if items and not isinstance(items[-1], Statement) and type(items[-1]) is cls:
            items[-1] = cls(items[-1] + chunk)
        else:
            items.append(cls(chunk))

    tokens: list[Token] = []
    pos = i = 0  # items cover body[:pos]; tokens cover body[pos:i]
    while i < len(body):
        t = read_token(body, i, symbols)
        end = i + len(t.raw)
        pending_plain = all(x.kind == "line" for x in tokens)
        if t.kind in IMPLICIT_ENDS and tokens and tokens[-1].kind == "callclose" and not pending_plain:
            # A call can stand as a statement with no 4b (a base constructor's call in an initializer list, or a
            # destructor after it): what follows, a marker, a line, an operand or an undecoded byte, begins the next.
            statement = _build(tokens, b"")
            if statement is not None:
                items.append(statement)
                tokens, pos = [], i
        if t.kind == "callclose" and not tokens:  # the end of a switch's case table
            put(body[pos:i], _classify(body[pos:i]))
            put(body[i:end], Marker)
            tokens, pos = [], end
        elif t.kind == "bad":
            put(body[pos:end], bytes)
            tokens, pos = [], end
        elif (t.kind in STATEMENT_ENDS and not (t.kind == "store" and pending_plain)) or (t.kind == "ret" and not pending_plain):
            # a value that a return leaves without a store (a constructor returns this), or a statement's own end
            statement = _build(tokens, t.raw)
            if statement is None:
                put(body[pos:end], bytes)
            else:
                items.append(statement)
            tokens, pos = [], end
        elif t.kind in STATEMENT_ENDS or t.kind in BOUNDARY or t.kind == "block":  # a goto, or a marker
            kind = "goto" if t.kind == "store" else t.kind
            put(body[pos:i], _classify(body[pos:i]))
            put(body[i:end], Scope if kind == "block" else Marker)
            tokens, pos = [], end
        else:
            tokens.append(t)
        i = end
    put(body[pos:], _classify(body[pos:]) if all(x.kind == "line" for x in tokens) else bytes)
    return tuple(items)


@dataclass(frozen=True)
class Effects:
    """What a statement reads and writes. Locals are keyed by handle; `mem_read` and `mem_write` stand for every
    other location. `barrier` marks a statement that may do anything: a call of a function that is not a trivial
    accessor, or a statement that ends in a branch or a return store."""

    reads: frozenset[bytes] = frozenset()
    writes: frozenset[bytes] = frozenset()
    mem_read: bool = False
    mem_write: bool = False
    barrier: bool = False


def _visit(node: Node, symbols: Symbols, acc: dict, lhs: bool = False) -> None:
    """Add a tree's reads and writes to acc. A local whose address is taken (`addressed`) may be written through
    a pointer, so it counts as memory."""
    if node.kind == "sym":
        if node.symbol.kind == "local":
            if not lhs:
                _read_local(node.symbol.handle, acc)
        elif node.symbol.kind in ("data", "unknown") and not lhs:
            acc["mem_read"] = True
    elif node.kind == "const":
        return
    elif node.kind in ("load", "this"):
        child = node.children[0]
        if child.kind == "sym" and child.symbol.kind == "local":
            _read_local(child.symbol.handle, acc)
        else:
            _visit(child, symbols, acc)
            acc["mem_read"] = acc["mem_read"] or node.kind == "load"
    elif node.kind in ASSIGNS:
        left, right = node.children
        compound = node.kind != "assign"  # a compound also reads its left side
        _visit(right, symbols, acc)
        if left.kind == "sym" and left.symbol.kind == "local":
            if left.symbol.handle in acc["addressed"]:
                acc["mem_write"] = True
                acc["mem_read"] = acc["mem_read"] or compound
            else:
                if compound:
                    acc["reads"].add(left.symbol.handle)
                acc["writes"].add(left.symbol.handle)
        else:
            _visit(left, symbols, acc, lhs=not compound)
            acc["mem_write"] = True
            acc["mem_read"] = acc["mem_read"] or compound
    elif node.kind == "call":
        callee = node.children[0]
        if callee.kind == "sym" and symbols.is_accessor(callee.symbol):
            acc["mem_read"] = True
        else:
            acc["barrier"] = True
            if callee.kind != "sym":  # a call through a computed pointer reads it
                _visit(callee, symbols, acc)
        for child in node.children[1:]:
            _visit(child, symbols, acc)
    else:
        for child in node.children:
            _visit(child, symbols, acc)


def _read_local(handle: bytes, acc: dict) -> None:
    if handle in acc["addressed"]:
        acc["mem_read"] = True
    else:
        acc["reads"].add(handle)


def _effects(node: Node, symbols: Symbols, addressed: frozenset[bytes]) -> Effects:
    acc: dict = {"reads": set(), "writes": set(), "mem_read": False, "mem_write": False, "barrier": False, "addressed": addressed}
    _visit(node, symbols, acc)
    return Effects(frozenset(acc["reads"]), frozenset(acc["writes"]), acc["mem_read"], acc["mem_write"], acc["barrier"])


def effects(statement: Statement, symbols: Symbols, addressed: frozenset[bytes] = frozenset()) -> Effects:
    found = _effects(statement.root, symbols, addressed)
    return found if statement.tail == bytes([END]) else replace(found, barrier=True)


def independent(first: Effects, second: Effects) -> bool:
    """Whether two adjacent statements, in either order, leave the same result: no barrier, no local written by
    one and read or written by the other, and no memory write alongside any memory access."""
    if first.barrier or second.barrier:
        return False
    if first.writes & (second.reads | second.writes) or second.writes & first.reads:
        return False
    return not ((first.mem_write and (second.mem_read or second.mem_write)) or (second.mem_write and first.mem_read))


def conflicts_with_value(between: Effects, value: Effects) -> bool:
    """Whether a statement between a definition and a use changes what the definition's value reads."""
    return between.barrier or bool(between.writes & value.reads) or (between.mem_write and value.mem_read)


def pretty(node: Node) -> str:
    """The expression as the source reads it, with locals by name and member addresses as offsets."""
    if node.kind == "sym":
        return node.symbol.short
    if node.kind == "const":
        return str(node.value)
    if node.kind == "select":
        return f"({pretty(node.children[0])} ? {pretty(node.children[1])} : {pretty(node.children[2])})"
    if node.kind in ("this", "conv", "argsep"):
        return pretty(node.children[0])
    if node.kind == "load":
        child = node.children[0]
        return pretty(child) if child.kind in ("sym", "this") else f"*({pretty(child)})"
    if node.kind in PREFIX:
        return f"{PREFIX[node.kind]}{pretty(node.children[0])}"
    if node.kind == "off":
        return f"({pretty(node.children[0])} + {pretty(node.children[1])})"
    if node.kind == "idx":
        return f"{pretty(node.children[0])}[{pretty(node.children[1])}]"
    if node.kind == "call":
        fixed, args = node.children[: node.fixed], node.children[node.fixed :]
        callee = fixed[0].symbol.short if fixed[0].kind == "sym" else f"({pretty(fixed[0])})"
        target = f"{pretty(fixed[1])}->{callee}" if node.fixed == 2 else callee
        return f"{target}({', '.join(pretty(a) for a in args)})"
    return f"{pretty(node.children[0])} {SYMBOL[node.kind]} {pretty(node.children[1])}"


@dataclass(frozen=True)
class Edit:
    """One legal change: its kind, a readable description, and the source lines it touches."""

    kind: str  # move, swap or replace
    text: str
    lines: tuple[int | None, ...]


def accessors_used(items: tuple[Item, ...], symbols: Symbols) -> list[str]:
    """The trivial accessors the statements call, by name, sorted."""
    names: set[str] = set()
    stack = [item.root for item in items if isinstance(item, Statement)]
    while stack:
        node = stack.pop()
        if node.kind == "call" and node.children[0].kind == "sym" and symbols.is_accessor(node.children[0].symbol):
            names.add(node.children[0].symbol.short)
        stack.extend(node.children)
    return sorted(names)


def _runs(items: tuple[Item, ...]) -> list[list[int]]:
    """Maximal runs of consecutive statements: a statement moves only within its run."""
    runs: list[list[int]] = []
    for index, item in enumerate(items):
        if isinstance(item, bytes):
            continue
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def _moves(items: tuple[Item, ...], symbols: Symbols, run: list[int], addressed: frozenset[bytes]) -> list[tuple[Edit, tuple[Item, ...]]]:
    out: list[tuple[Edit, tuple[Item, ...]]] = []
    eff = {i: effects(items[i], symbols, addressed) for i in run}
    for position, index in enumerate(run):
        mover = items[index]
        for target in run[position + 1 :]:  # later: pass the statements the mover is independent of
            if not independent(eff[index], eff[target]):
                break
            new = list(items)
            del new[index]
            new.insert(target, mover)
            text = f"move `{pretty(mover.root)}` (line {mover.line}) after `{pretty(items[target].root)}` (line {items[target].line})"
            out.append((Edit("move", text, (mover.line, items[target].line)), tuple(new)))
        for target in reversed(run[:position]):  # earlier
            if not independent(eff[target], eff[index]):
                break
            new = list(items)
            del new[index]
            new.insert(target, mover)
            text = f"move `{pretty(mover.root)}` (line {mover.line}) before `{pretty(items[target].root)}` (line {items[target].line})"
            out.append((Edit("move", text, (mover.line, items[target].line)), tuple(new)))
    return out


def _with_root(items: tuple[Item, ...], index: int, root: Node) -> tuple[Item, ...]:
    old = items[index]
    new = list(items)
    new[index] = Statement(old.prefix, old.line, root, old.tail)
    return tuple(new)


def _nodes(node: Node, path: tuple[int, ...] = ()):
    yield path, node
    for k, child in enumerate(node.children):
        yield from _nodes(child, path + (k,))


def _replace_at(node: Node, path: tuple[int, ...], new: Node) -> Node:
    if not path:
        return new
    children = list(node.children)
    children[path[0]] = _replace_at(children[path[0]], path[1:], new)
    return Node(node.raw, node.kind, tuple(children), node.symbol, node.value, node.fixed)


def _swaps(items: tuple[Item, ...], symbols: Symbols, index: int, addressed: frozenset[bytes]) -> list[tuple[Edit, tuple[Item, ...]]]:
    """Each add or multiply whose two operands write nothing, swapped in place: operands that write nothing can run
    in either order. Call arguments are not swapped: their order in the IL is their parameter position, so a swap
    passes the callee other values (swapping `CellFlagsAt(x, y)`'s arguments in the IL and in C++ gives the same
    code)."""
    statement = items[index]
    out: list[tuple[Edit, tuple[Item, ...]]] = []

    def pure(node: Node) -> bool:
        found = _effects(node, symbols, addressed)
        return not (found.writes or found.mem_write or found.barrier)

    for path, node in _nodes(statement.root):
        if node.kind in ("add", "mul"):
            left, right = node.children
            if left.emit() == right.emit() or not (pure(left) and pure(right)):
                continue
            swapped = Node(node.raw, node.kind, (right, left))
            text = f"swap the operands of `{pretty(node)}` (line {statement.line}): `{pretty(left)}` and `{pretty(right)}`"
            out.append((Edit("swap", text, (statement.line,)), _with_root(items, index, _replace_at(statement.root, path, swapped))))
    return out


def _parent(root: Node, path: tuple[int, ...]) -> Node | None:
    if not path:
        return None
    node = root
    for k in path[:-1]:
        node = node.children[k]
    return node


def _local_uses(items: tuple[Item, ...]) -> tuple[dict[bytes, list[int]], frozenset[bytes]]:
    """For each local: the statements that write it. Also the locals whose address is taken: a local used other
    than as the operand of a load or this, or as the left side of a write."""
    defs: dict[bytes, list[int]] = {}
    addressed: set[bytes] = set()
    for index, item in enumerate(items):
        if isinstance(item, bytes):
            continue
        for path, node in _nodes(item.root):
            if node.kind != "sym" or node.symbol.kind != "local":
                continue
            parent = _parent(item.root, path)
            if parent is not None and parent.kind in ASSIGNS and parent.children[0] is node:
                defs.setdefault(node.symbol.handle, []).append(index)
            elif parent is None or parent.kind not in ("load", "this"):
                addressed.add(node.symbol.handle)
    return defs, frozenset(addressed)


def _replacements(items: tuple[Item, ...], symbols: Symbols, addressed: frozenset[bytes]) -> list[tuple[Edit, tuple[Item, ...]]]:
    """Each use of a local with the value its one definition assigns, when that value writes nothing and nothing
    between the definition and the use changes what it reads. Block markers between them change no value; any
    other marker or opaque bytes end the region."""
    defs, addressed_locals = _local_uses(items)
    out: list[tuple[Edit, tuple[Item, ...]]] = []
    for handle, sites in defs.items():
        if len(sites) != 1 or handle in addressed_locals:
            continue
        d = sites[0]
        definition = items[d]
        if definition.root.kind != "assign" or definition.tail != bytes([END]):
            continue
        value = definition.root.children[1]
        if value.kind == "sym" and value.symbol.handle == handle:
            continue
        value_effects = _effects(value, symbols, addressed)
        if value_effects.writes or value_effects.mem_write or value_effects.barrier:
            continue
        name = definition.root.children[0].symbol.short
        for u in range(d + 1, len(items)):
            if isinstance(items[u], Scope):
                continue
            if isinstance(items[u], bytes):
                break
            between = items[d + 1 : u]
            if any(isinstance(other, bytes) and not isinstance(other, Scope) for other in between):
                break
            if any(not isinstance(other, bytes) and conflicts_with_value(effects(other, symbols, addressed), value_effects) for other in between):
                break
            item = items[u]
            for path, node in _nodes(item.root):
                if node.kind == "load" and node.children[0].kind == "sym" and node.children[0].symbol.handle == handle:
                    text = f"replace the use of `{name}` (line {item.line}) with `{pretty(value)}`, its value at line {definition.line}"
                    out.append((Edit("replace", text, (item.line, definition.line)), _with_root(items, u, _replace_at(item.root, path, value))))
    return out


def neighbours(items: tuple[Item, ...], symbols: Symbols) -> list[tuple[Edit, tuple[Item, ...]]]:
    """Every legal single edit of a parsed body, each with the body it makes."""
    out: list[tuple[Edit, tuple[Item, ...]]] = []
    addressed = _local_uses(items)[1]
    for run in _runs(items):
        out += _moves(items, symbols, run, addressed)
    for index, item in enumerate(items):
        if isinstance(item, Statement):
            out += _swaps(items, symbols, index, addressed)
    out += _replacements(items, symbols, addressed)
    return out
