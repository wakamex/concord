"""Trace differing instructions to observed GCC RTL decisions and source lines.

The target supplies an assembly constraint, not compiler dumps. A trace proves
where our instruction was introduced or assigned registers; it does not prove
which source edit would make the target's compiler decision.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from itertools import pairwise
from pathlib import Path

from concord.diff import read_function
from concord.harvest import Harvest
from concord.model import Cause

SCHEMA = "concord.causal.v1"
PASS = re.compile(r"\.(\d+)r\.([\w_]+)$")
HEAD = re.compile(r"^;; Function (.+)$", re.MULTILINE)
INSN = re.compile(r"^\((?:insn|jump_insn|call_insn) (\d+) ", re.MULTILINE)
REGISTER = re.compile(r"\(reg(?:/[a-z]+)?:\w+\s+(\d+)(?:\s+([a-z][a-z0-9]*))?")


def expression(text: str, start: int) -> str:
    """One balanced RTL expression, ignoring parentheses in quoted strings."""
    depth, quoted, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    raise ValueError("unterminated RTL expression")


def rtl_function(text: str, symbol: str) -> dict[int, dict]:
    headers = list(HEAD.finditer(text))
    matches = [i for i, h in enumerate(headers) if re.search(r"\(" + re.escape(symbol) + r"(?:[, )])", h[1])]
    if len(matches) != 1:
        return {}
    index = matches[0]
    chunk = text[headers[index].end():headers[index + 1].start() if index + 1 < len(headers) else len(text)]
    records = {}
    for match in INSN.finditer(chunk):
        record = expression(chunk, match.start())
        opening = record.find('(', 1)
        if opening < 0:
            continue
        pattern = expression(record, opening)
        location = re.search(r"([^\s()]+):(\d+)(?:\s|$)", record[:opening])
        registers = [{"number": int(m[1]), "hard": m[2]} for m in REGISTER.finditer(pattern)]
        values = sorted({(m[1] or m[2]).strip() for m in re.finditer(r"\[orig:\d+ ([^\]]+)\]|\[ ([^\[\]]*[A-Za-z_][^\[\]]*) \]", pattern)})
        records[int(match[1])] = {
            "pattern": " ".join(pattern.split()), "registers": registers, "values": values,
            "source": {"path": location[1], "line": int(location[2])} if location else None,
        }
    return records


def listing_instructions(text: str, symbol: str, start: int, code: bytes) -> dict[int, dict]:
    """Join assembler section offsets to GCC instruction IDs, verifying bytes."""
    active = False
    records = {}
    for line in text.splitlines():
        label = re.search(r"\t([.$\w]+):\s*$", line)
        if label and label[1] == symbol:
            active = True
        elif label and not label[1].startswith('.L'):
            active = False
        if not active:
            continue
        match = re.match(r"\s*\d+\s+([\da-fA-F]+)\s+([\da-fA-F]+)\s+(.+?)\s+#\s*(\d+)\b", line)
        if not match:
            continue
        offset = int(match[1], 16) - start
        data = bytes.fromhex(match[2])
        if not 0 <= offset < len(code) or code[offset:offset + len(data)] != data:
            raise ValueError(f"assembler listing bytes disagree at {symbol}+{offset:#x}")
        records[offset] = {"uid": int(match[4]), "assembly": match[3].strip()}
    return records


def complete_listing(listing: str, assembly: str) -> str:
    """Restore full assembler lines using the listing's input line numbers.

    Old assemblers truncate long symbols in listings. Their listing line
    numbers still identify the original, complete compiler assembly lines.
    """
    source = assembly.splitlines()
    lines = []
    for line in listing.splitlines():
        match = re.match(r'^(\s*(\d+)\s+[^\t]*)\t', line)
        if match and 0 < int(match[2]) <= len(source):
            line = match[1] + '\t' + source[int(match[2]) - 1]
        lines.append(line)
    return '\n'.join(lines)


def histories(directory: Path, symbol: str, wanted: set[int]) -> dict[int, list[dict]]:
    return histories_many(directory, {symbol: wanted})[symbol]


def histories_many(directory: Path, wanted: dict[str, set[int]]) -> dict[str, dict[int, list[dict]]]:
    paths = sorted((p for p in directory.iterdir() if PASS.search(p.name)), key=lambda p: int(PASS.search(p.name)[1]))
    history = {symbol: {uid: [] for uid in ids} for symbol, ids in wanted.items()}
    previous = {symbol: {} for symbol in wanted}
    for path in paths:
        number, name = PASS.search(path.name).groups()
        text = path.read_text(errors="replace")
        for symbol, ids in wanted.items():
            rows = rtl_function(text, symbol)
            common = rows.keys() & previous[symbol].keys()
            before_order = [uid for uid in previous[symbol] if uid in common]
            after_order = [uid for uid in rows if uid in common]
            moved = {a for a, b in zip(before_order, after_order, strict=True) if a != b}
            for uid in ids & rows.keys():
                row = {**rows[uid], 'order_changed': uid in moved}
                steps = history[symbol][uid]
                if steps and row['pattern'] == steps[-1]['pattern'] and not row['order_changed']:
                    continue
                steps.append({"pass": name, "pass_number": int(number), "dump": str(path), **row})
            previous[symbol] = rows
    return history


def decision(history: list[dict], cause: Cause) -> dict | None:
    if not history:
        return None
    if cause == Cause.BLOCK_ORDER:
        moves = [(a, b) for a, b in pairwise(history) if b.get('order_changed')]
        if moves:
            before, after = moves[-1]
            return {"kind": "instruction-order-change", "before": before, "after": after}
    if cause == Cause.REGISTER_ALLOCATION:
        # A name such as ax or xmm0 distinguishes a physical register in GCC's
        # RTL from a pseudo-register, without assuming a target register count.
        for before, after in pairwise(history):
            previous = before['registers']
            current = after['registers']
            if any(not r['hard'] for r in previous) and all(r['hard'] for r in current) and current:
                return {"kind": "physical-register-assignment", "before": before, "after": after}
    return {"kind": "instruction-introduction", "before": None, "after": history[0]}


def source_construct(root: Path, location: dict | None) -> dict | None:
    if not location:
        return None
    path = Path(location['path'])
    if str(path).startswith('/work/'):
        path = Path(str(path).removeprefix('/work/'))
    path = root / path
    result = dict(location)
    if path.is_file() and path.resolve().is_relative_to(root.resolve()):
        lines = path.read_text(errors='replace').splitlines()
        if 0 < location['line'] <= len(lines):
            result['text'] = lines[location['line'] - 1].strip()
    return result


def analyze(harvest: Harvest, unit: str, symbol: str, directory: Path, *, limit: int | None = 8, offsets: set[int] | None = None) -> dict:
    return analyze_many(harvest, unit, [symbol], directory, limit=limit, offsets=offsets)[0]


def analyze_many(harvest: Harvest, unit: str, symbols: list[str], directory: Path, *, limit: int | None = None, offsets: set[int] | None = None) -> list[dict]:
    contexts = {}
    for symbol in symbols:
        canonical = read_function(harvest.objects / 'base' / f'{harvest.slug(unit)}.o', symbol)
        traced = read_function(directory / 'trace.o', symbol)
        if canonical.code != traced.code or canonical.relocations != traced.relocations:
            raise ValueError('diagnostic flags changed this function; its dumps cannot explain the canonical compile')
        contexts[symbol] = (canonical, traced)
    metadata = json.loads((directory / 'metadata.json').read_text())
    verify_inputs(harvest.root, unit, metadata)
    for name, digest in metadata.get('artifacts', {}).items():
        path = directory / name
        if path.parent != directory or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'dump artifact changed: {name}')
    listing = (directory / 'listing.txt').read_text(errors='replace')
    if (directory / 'trace.s').is_file():
        listing = complete_listing(listing, (directory / 'trace.s').read_text(errors='replace'))
    mappings, selections, wanted = {}, {}, {}
    verdicts = {v.symbol: v for v in harvest.verdicts(unit)}
    for symbol, (_, traced) in contexts.items():
        difference = harvest.diff(verdicts[symbol])
        mapping = listing_instructions(listing, symbol, traced.start, traced.code)
        selected = [f for f in difference.findings if f.cause != Cause.INLINING and (offsets is None or f.offset in offsets)][:limit]
        mappings[symbol], selections[symbol] = mapping, selected
        wanted[symbol] = {mapping[f.candidate]['uid'] for f in selected if f.candidate in mapping}
    history = histories_many(directory, wanted)
    return [_report(harvest, unit, symbol, directory, contexts[symbol][0], metadata,
                    mappings[symbol], selections[symbol], history[symbol]) for symbol in symbols]


def _report(harvest, unit, symbol, directory, canonical, metadata, mapping, selected, history):
    findings = []
    for f in selected:
        row = {"cause": f.cause.value, "target_offset": f.offset, "candidate_offset": f.candidate,
               "target_constraint": f.detail, "status": "unresolved", "decision": None, "source": None}
        if f.candidate in mapping:
            instruction = mapping[f.candidate]
            steps = history[instruction['uid']]
            observed = decision(steps, f.cause)
            row.update({"instruction": instruction, "history": steps, "decision": observed})
            if observed:
                row['status'] = 'observed-candidate-decision'
                row['source'] = source_construct(harvest.root, next((s['source'] for s in reversed(steps) if s['source']), None))
        findings.append(row)
    return {"schema": SCHEMA, "unit": unit, "symbol": symbol, "directory": str(directory),
            "canonical_code_sha256": hashlib.sha256(canonical.code).hexdigest(),
            "diagnostic_code_verified": True, "metadata": metadata,
            "findings": findings}


def verify_inputs(root: Path, unit: str, metadata: dict) -> None:
    inputs = metadata.get('inputs', {})
    if 'src/' + unit not in inputs:
        raise ValueError('dump metadata does not name this unit')
    for name, digest in inputs.items():
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'dump source input changed: {name}')


def collect(harvest: Harvest, unit: str) -> Path:
    out = harvest.root / 'build' / 'concord' / 'causal' / uuid.uuid4().hex
    done = subprocess.run(['uv', '--no-config', 'run', '--locked', 'python', str(Path(__file__).with_name('causal_driver.py')), unit, str(out)],
                          cwd=harvest.root, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f'pinned dump collection failed:\n{done.stderr[-4000:]}')
    json.loads(done.stdout)
    return out


def show(report: dict) -> list[str]:
    lines = [f"{report['unit']}: {report['symbol']}"]
    for row in report['findings']:
        if not row['decision']:
            lines.append(f"  +{row['target_offset']:#x}: {row['cause']}: no traced candidate instruction")
            continue
        stage = row['decision']['after']
        source = row['source'] or {}
        lines.append(f"  +{row['target_offset']:#x}: {stage['pass']} ({row['decision']['kind']}), "
                     f"{source.get('path', '?')}:{source.get('line', '?')}: {source.get('text', '')}")
    return lines
