"""MSVC 5.0's intermediate language (IL) for one function: a parser that splits a function's `ex` body into
statements and expression trees, a writer that turns them back into the same bytes, and the effect analysis
that decides which edits to them are legal.

A body is postfix bytecode: each operand comes before the operator that consumes it, and a statement ends
with 4b. Opcodes decoded from captures (probe/IL-NOTES.md, and the tileswitchlogic, brickz and namerecord
captures this module is tested on):

    26 H              symbol reference; H is 2 bytes, or 4 bytes when its last two are 01 00
    33 [a0] T V       constant V of type T, V below 0x80
    30 [a0] T         load of the operand as type T
    5a [a0] T         the operand as a pointer value (a `this` read)
    27 T              member address: base pointer, then a constant offset
    02, 04, 24        add, multiply, greater-than
    28 00 00          array element: base, then index
    32 [a0] T         assign: left side, then right side
    4f 01 L           line marker, L one byte or 80 followed by two bytes
    callee this 99 T 3e U 14 args... 4c
                      a call: the callee and `this` are pushed first, then the arguments, and 4c ends the call

Any byte the parser does not decode ends its statement early: that statement and the bytes up to the next 4b are
opaque, and opaque bytes are never moved or edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TYPES = frozenset({0x41, 0x42, 0x43})  # int, the second scalar type, pointer
END = 0x4B
CALL_CLOSE = 0x4C
LOCAL = re.compile(rb"\x01.(....|..)\x00([A-Za-z_][\x21-\x7e]*)\x00", re.DOTALL)  # sy: 01 BB HANDLE 00 name 00, HANDLE 4 or 2 bytes
GLOBAL = re.compile(rb"([?][\x21-\x7e]{3,})\x00")  # gl: handle bytes 00 ?name 00
SCOPE = re.compile(rb"(?:\x4f\x01(?:\x80..|.)|\x53)*", re.DOTALL)  # block begins and line markers only


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # "local", "data" or "function"
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
    kind: str  # sym, const, load, this, off, add, mul, gt, idx, assign, line, end, callopen, callclose, bad
    raw: bytes
    symbol: Symbol | None = None
    value: int | None = None  # a constant's value or a line number


@dataclass(frozen=True)
class Node:
    """An operator or leaf. Operands come before `raw` in the bytes; a call's arguments come after its opening
    bytes, which `raw` holds, and the call closes with 4c."""

    raw: bytes
    kind: str  # sym, const, load, this, off, add, mul, gt, idx, assign, call
    children: tuple[Node, ...] = ()
    symbol: Symbol | None = None
    value: int | None = None

    def emit(self) -> bytes:
        if self.kind == "call":
            callee, this, *args = self.children
            return callee.emit() + this.emit() + self.raw + b"".join(a.emit() for a in args) + bytes([CALL_CLOSE])
        return b"".join(child.emit() for child in self.children) + self.raw


@dataclass(frozen=True)
class Statement:
    prefix: bytes  # line markers before the statement
    line: int | None
    root: Node

    def emit(self) -> bytes:
        return self.prefix + self.root.emit() + bytes([END])


class Scope(bytes):
    """Opaque bytes made only of block-begin markers (53) and line markers: no value changes across them, but no
    statement moves across them, since a statement stays in its block."""


Item = Statement | bytes  # other bytes are opaque: the parser did not decode them, and no edit touches them


def emit_body(items: tuple[Item, ...]) -> bytes:
    return b"".join(item if isinstance(item, bytes) else item.emit() for item in items)


def read_token(body: bytes, i: int, symbols: Symbols) -> Token:
    def at(k: int) -> int | None:
        return body[i + k] if i + k < len(body) else None

    c = body[i]
    if c == 0x26:
        width = 4 if body[i + 3 : i + 5] == b"\x01\x00" else 2
        handle = body[i + 1 : i + 1 + width]
        symbol = symbols.lookup(handle) if len(handle) == width else None
        if symbol is not None:
            return Token("sym", body[i : i + 1 + width], symbol)
    elif c == 0x33:
        n = 4 if at(1) == 0xA0 else 3
        value = at(n - 1)
        if at(n - 2) in TYPES and value is not None and value < 0x80:
            return Token("const", body[i : i + n], value=value)
    elif c in (0x30, 0x5A, 0x32):
        n = 3 if at(1) == 0xA0 else 2
        if at(n - 1) in TYPES:
            return Token({0x30: "load", 0x5A: "this", 0x32: "assign"}[c], body[i : i + n])
    elif c == 0x27 and at(1) in TYPES:
        return Token("off", body[i : i + 2])
    elif c in (0x02, 0x04, 0x24):
        return Token({0x02: "add", 0x04: "mul", 0x24: "gt"}[c], body[i : i + 1])
    elif c == 0x28 and body[i : i + 3] == b"\x28\x00\x00":
        return Token("idx", body[i : i + 3])
    elif c == END:
        return Token("end", body[i : i + 1])
    elif c == CALL_CLOSE:
        return Token("callclose", body[i : i + 1])
    elif c == 0x4F and at(1) == 0x01 and at(2) is not None:
        n = 5 if at(2) == 0x80 else 3
        if i + n <= len(body):
            value = int.from_bytes(body[i + 3 : i + 5], "little") if n == 5 else body[i + 2]
            return Token("line", body[i : i + n], value=value)
    elif c == 0x99 and at(1) in TYPES and at(2) == 0x3E and at(3) in TYPES and at(4) == 0x14:
        return Token("callopen", body[i : i + 5])
    return Token("bad", body[i : i + 1])


def _build(tokens: list[Token]) -> Statement | None:
    """The statement a run of tokens ending before 4b makes, or None when the run is not one whole expression."""
    prefix = bytearray()
    line = None
    while tokens and tokens[0].kind == "line":
        prefix += tokens[0].raw
        line = tokens[0].value
        tokens = tokens[1:]
    stack: list[Node] = []
    frames: list[tuple[int, bytes]] = []  # for each open call: the stack height before its arguments, and its bytes
    for t in tokens:
        if t.kind == "sym":
            stack.append(Node(t.raw, "sym", (), t.symbol))
        elif t.kind == "const":
            stack.append(Node(t.raw, "const", (), None, t.value))
        elif t.kind in ("load", "this"):
            if not stack:
                return None
            stack.append(Node(t.raw, t.kind, (stack.pop(),)))
        elif t.kind in ("off", "add", "mul", "gt", "idx", "assign"):
            if len(stack) < 2:
                return None
            right, left = stack.pop(), stack.pop()
            stack.append(Node(t.raw, t.kind, (left, right)))
        elif t.kind == "callopen":
            if len(stack) < 2:
                return None
            frames.append((len(stack), t.raw))
        elif t.kind == "callclose":
            if not frames:
                return None
            height, raw = frames.pop()
            args = stack[height:]
            del stack[height:]
            if len(stack) < 2:
                return None
            this = stack.pop()
            callee = stack.pop()
            if callee.kind != "sym":
                return None
            stack.append(Node(raw, "call", (callee, this, *args)))
        else:  # line markers inside an expression, undecoded bytes
            return None
    if frames or len(stack) != 1:
        return None
    return Statement(bytes(prefix), line, stack[0])


def parse(body: bytes, symbols: Symbols) -> tuple[Item, ...]:
    """A function body as items: statements, and opaque bytes between them. emit_body gives back `body`. An
    undecoded byte ends the statement before it, so one unknown opcode makes only its own statement opaque."""
    items: list[Item] = []
    opaque = bytearray()

    def flush() -> None:
        if opaque:
            chunk = bytes(opaque)
            items.append(Scope(chunk) if SCOPE.fullmatch(chunk) else chunk)
            opaque.clear()

    tokens: list[Token] = []
    start = i = 0
    while i < len(body):
        token = read_token(body, i, symbols)
        i += len(token.raw)
        if token.kind == "bad":
            opaque += body[start:i]
            tokens, start = [], i
            continue
        if token.kind == "end":
            statement = _build(tokens)
            if statement is None:
                opaque += body[start:i]
            else:
                flush()
                items.append(statement)
            tokens, start = [], i
        else:
            tokens.append(token)
    opaque += body[start:]
    flush()
    return tuple(items)


@dataclass(frozen=True)
class Effects:
    """What a statement reads and writes. Locals are keyed by handle; `mem_read` and `mem_write` stand for every
    other location. `barrier` marks a call of a function that is not a trivial accessor, which may do anything."""

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
        elif node.symbol.kind == "data" and not lhs:
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
    elif node.kind == "assign":
        left, right = node.children
        _visit(right, symbols, acc)
        if left.kind == "sym" and left.symbol.kind == "local":
            if left.symbol.handle in acc["addressed"]:
                acc["mem_write"] = True
            else:
                acc["writes"].add(left.symbol.handle)
        else:
            _visit(left, symbols, acc, lhs=True)
            acc["mem_write"] = True
    elif node.kind == "call":
        callee = node.children[0]
        if symbols.is_accessor(callee.symbol):
            acc["mem_read"] = True
        else:
            acc["barrier"] = True
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
    return _effects(statement.root, symbols, addressed)


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
    if node.kind == "this":
        return pretty(node.children[0])
    if node.kind == "load":
        child = node.children[0]
        return pretty(child) if child.kind in ("sym", "this") else f"*({pretty(child)})"
    if node.kind == "off":
        return f"({pretty(node.children[0])} + {pretty(node.children[1])})"
    if node.kind == "idx":
        return f"{pretty(node.children[0])}[{pretty(node.children[1])}]"
    if node.kind == "call":
        callee, this, *args = node.children
        return f"{pretty(this)}->{callee.symbol.short}({', '.join(pretty(a) for a in args)})"
    symbol = {"add": "+", "mul": "*", "gt": ">", "assign": "="}[node.kind]
    return f"{pretty(node.children[0])} {symbol} {pretty(node.children[1])}"


@dataclass(frozen=True)
class Edit:
    """One legal change: its kind, a readable description, and the source lines it touches."""

    kind: str  # move, swap or replace
    text: str
    lines: tuple[int | None, ...]


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
    new[index] = Statement(old.prefix, old.line, root)
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
    return Node(node.raw, node.kind, tuple(children), node.symbol, node.value)


def _swaps(items: tuple[Item, ...], symbols: Symbols, index: int, addressed: frozenset[bytes]) -> list[tuple[Edit, tuple[Item, ...]]]:
    """Each add or multiply whose two operands write nothing, and each pair of call arguments that write nothing,
    swapped in place: operands that write nothing can run in either order."""
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
        elif node.kind == "call":
            for i in range(2, len(node.children)):
                for j in range(i + 1, len(node.children)):
                    a, b = node.children[i], node.children[j]
                    if a.emit() == b.emit() or not (pure(a) and pure(b)):
                        continue
                    new_children = list(node.children)
                    new_children[i], new_children[j] = b, a
                    swapped = Node(node.raw, "call", tuple(new_children))
                    text = f"swap arguments {i - 1} and {j - 1} of `{pretty(node)}` (line {statement.line})"
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
    """For each local: the statements that assign it. Also the locals whose address is taken: a local used other
    than as the operand of a load or this, or as the left side of an assignment."""
    defs: dict[bytes, list[int]] = {}
    addressed: set[bytes] = set()
    for index, item in enumerate(items):
        if isinstance(item, bytes):
            continue
        for path, node in _nodes(item.root):
            if node.kind != "sym" or node.symbol.kind != "local":
                continue
            parent = _parent(item.root, path)
            if parent is not None and parent.kind == "assign" and parent.children[0] is node:
                defs.setdefault(node.symbol.handle, []).append(index)
            elif parent is None or parent.kind not in ("load", "this"):
                addressed.add(node.symbol.handle)
    return defs, frozenset(addressed)


def _replacements(items: tuple[Item, ...], symbols: Symbols, addressed: frozenset[bytes]) -> list[tuple[Edit, tuple[Item, ...]]]:
    """Each use of a local with the value its one definition assigns, when that value writes nothing and nothing
    between the definition and the use changes what it reads. Scope markers between them change no value; any
    other undecoded bytes end the region."""
    defs, addressed_locals = _local_uses(items)
    out: list[tuple[Edit, tuple[Item, ...]]] = []
    for handle, sites in defs.items():
        if len(sites) != 1 or handle in addressed_locals:
            continue
        d = sites[0]
        definition = items[d]
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
