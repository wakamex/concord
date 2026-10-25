"""Stack partitions and target slot constraints from verified compiler dumps."""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import capstone
from elftools.elf.elffile import ELFFile

from concord import causal, scores
from concord.diff import diff_code, disassemble, read_function, shape
from concord.harvest import BUILD, Harvest
from concord.lines import inline_chains
from concord.model import Cause
from concord.seed import _extents


def partitions(text: str, symbol: str) -> list[dict]:
    headers = list(causal.HEAD.finditer(text))
    matches = [i for i, h in enumerate(headers) if re.search(r'\(' + re.escape(symbol) + r'(?:[, )])', h[1])]
    if len(matches) != 1:
        return []
    i = matches[0]
    chunk = text[headers[i].end():headers[i + 1].start() if i + 1 < len(headers) else len(text)]
    found, current = [], None
    for line in chunk.splitlines():
        if match := re.fullmatch(r'Partition (\d+): size (\d+) align (\d+)', line):
            current = {'partition': int(match[1]), 'size': int(match[2]), 'alignment': int(match[3]), 'members': []}
            found.append(current)
        elif current is not None and (match := re.fullmatch(r'\s+(.+), offset (-?\d+)', line)):
            current['members'].append({'name': match[1], 'offset': int(match[2])})
        elif line.startswith(';;'):
            current = None
    records = causal.rtl_function(text, symbol)
    for partition in found:
        members = [re.compile(r'(?<![\w.])' + re.escape(m['name']) + r'(?!\w)') for m in partition['members']]
        refs = []
        for uid, record in records.items():
            if 'virtual-stack-vars' in record['pattern'] and any(p.search(record['pattern']) for p in members):
                offsets = re.findall(r'virtual-stack-vars\)\s*\(const_int (-?\d+)', record['pattern'])
                refs.append({'uid': uid, 'virtual_offsets': sorted({int(o) for o in offsets}), 'source': record['source']})
        partition['references'] = refs
    return found


def stack_operands(instruction) -> list[tuple]:
    return [o for o in instruction.operands if o[0] == 'mem' and o[1] == 'rsp']


def slot_pairs(target, candidate) -> list[dict]:
    """Aligned stack operands are constraints, not proof of source equivalence."""
    a, b = disassemble(target), disassemble(candidate)
    def key(instruction):
        mnemonic, operands = shape(instruction)
        operands = tuple(o[:4] + (0,) + o[5:] if o[0] == 'mem' and o[1] == 'rsp' else o for o in operands)
        return mnemonic, operands
    ka, kb = list(map(key, a)), list(map(key, b))
    matcher = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    found = []
    for start_a, start_b, size in matcher.get_matching_blocks():
        if size < 4:
            continue
        # Repeated construction sequences cannot uniquely identify a temporary.
        signature = tuple(ka[start_a:start_a + size])
        if sum(tuple(ka[i:i + size]) == signature for i in range(len(ka) - size + 1)) != 1:
            continue
        if sum(tuple(kb[i:i + size]) == signature for i in range(len(kb) - size + 1)) != 1:
            continue
        for x, y in zip(a[start_a:start_a + size], b[start_b:start_b + size], strict=True):
            sa, sb = stack_operands(x), stack_operands(y)
            if len(sa) != len(sb):
                continue
            for original, ours in zip(sa, sb, strict=True):
                if original[1:4] == ours[1:4] and original[5] == ours[5] and original[4] != ours[4]:
                    found.append({'target_offset': x.offset, 'candidate_offset': y.offset,
                                  'base': ours[1], 'target_slot': original[4], 'candidate_slot': ours[4], 'width': ours[5]})
    return found


def value_origins(records: dict[int, dict], names: list[str]) -> dict[str, dict]:
    """Follow single-definition pseudo-registers to named memory fields.

    Ambiguous writes and cycles remain unresolved instead of choosing a path.
    """
    definitions, labels = defaultdict(list), defaultdict(set)
    for record in records.values():
        pattern = record['pattern']
        for match in re.finditer(r'\(set (\(reg[^)]*\))', pattern):
            register = re.match(r'\(reg(?:/[^: ]+)?:\w+ (\d+)(?: \[([^]]+)\])?', match[1])
            if not register:
                continue
            start = match.end()
            while start < len(pattern) and pattern[start].isspace():
                start += 1
            if start >= len(pattern) or pattern[start] != '(':
                continue
            rhs = causal.expression(pattern, start)
            number = int(register[1])
            definitions[number].append((rhs, record['source']))
            if register[2]:
                labels[register[2].strip()].add(number)
    def follow(number, visited):
        if number in visited:
            return set(), set()
        values = {(rhs, json.dumps(location, sort_keys=True)) for rhs, location in definitions.get(number, [])}
        if len(values) != 1:
            return set(), set()
        rhs, location = next(iter(values))
        fields = set(re.findall(r'\[\d+ ([A-Za-z_$][\w.$]*)\+[-\d]+ S', rhs))
        sources = {location} if location != 'null' else set()
        for child in re.findall(r'\(reg(?:/[^: ]+)?:\w+ (\d+)', rhs):
            a, b = follow(int(child), visited | {number})
            fields.update(a)
            sources.update(b)
        return fields, sources
    result = {}
    for name in names:
        if len(labels.get(name, set())) == 1:
            fields, sources = follow(next(iter(labels[name])), set())
            result[name] = {'fields': sorted(fields), 'sources': [json.loads(s) for s in sorted(sources)]}
    return result


def callsite_source(root: Path, unit: str, symbol: str, line: int) -> dict:
    from concord.transforms import _descendants, find_function

    source = (root / 'src' / unit).read_bytes()
    scope = find_function(source, symbol)
    nodes = []
    for kind in ('declaration', 'expression_statement'):
        for node in _descendants(scope, kind):
            first = source.count(b'\n', 0, node.start_byte) + 1
            last = source.count(b'\n', 0, node.end_byte) + 1
            if first <= line <= last:
                nodes.append((node, first, last))
    if not nodes:
        return causal.source_construct(root, {'path': 'src/' + unit, 'line': line})
    node, first, last = min(nodes, key=lambda item: item[0].end_byte - item[0].start_byte)
    return {'path': 'src/' + unit, 'line': first, 'end_line': last, 'text': node.text.decode(errors='replace')}



def analyze(harvest: Harvest, unit: str, symbol: str, directory: Path) -> dict:
    metadata = json.loads((directory / 'metadata.json').read_text())
    causal.verify_inputs(harvest.root, unit, metadata)
    for name, digest in metadata.get('artifacts', {}).items():
        path = directory / name
        if path.parent != directory or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'dump artifact changed: {name}')
    candidate = read_function(directory / 'trace.o', symbol)
    canonical = read_function(harvest.objects / 'base' / f'{harvest.slug(unit)}.o', symbol)
    if candidate.code != canonical.code or candidate.relocations != canonical.relocations:
        raise ValueError('diagnostic flags changed this function')
    target = read_function(harvest.objects / 'target' / f'{harvest.slug(unit)}.o', symbol)
    expand = next(directory.glob('*.expand'))
    text = expand.read_text(errors='replace')
    groups = partitions(text, symbol)
    expand_records = causal.rtl_function(text, symbol)
    by_uid = defaultdict(list)
    for group in groups:
        for ref in group['references']:
            by_uid[ref['uid']].append(group['partition'])
    listing = (directory / 'listing.txt').read_text(errors='replace')
    listing = causal.complete_listing(listing, (directory / 'trace.s').read_text(errors='replace'))
    instructions = causal.listing_instructions(listing, symbol, candidate.start, candidate.code)
    constraints = slot_pairs(target, candidate)
    wanted = {instructions[r['candidate_offset']]['uid'] for r in constraints if r['candidate_offset'] in instructions}
    histories = causal.histories(directory, symbol, wanted)
    for row in constraints:
        insn = instructions.get(row['candidate_offset'])
        if insn is None:
            row['status'] = 'unmapped-instruction'
            continue
        uid = insn['uid']
        steps = histories.get(uid, [])
        decision = causal.decision(steps, Cause.STACK_LAYOUT)
        row.update(uid=uid, partitions=by_uid[uid], decision=decision,
                   source=causal.source_construct(harvest.root, next((s['source'] for s in reversed(steps) if s['source']), None)),
                   values=sorted({v for s in steps for v in s.get('values', [])}), status='observed-slot-constraint')
    chains = inline_chains(directory / 'trace.o', candidate.section, [candidate.start + r['candidate_offset'] for r in constraints])
    for row, chain in zip(constraints, chains, strict=True):
        row['inline_chain'] = [asdict(frame) for frame in chain]
        row['value_origins'] = value_origins(expand_records, row.get('values', []))
        callers = [frame for frame in chain if frame.path == 'src/' + unit]
        row['caller_source'] = callsite_source(harvest.root, unit, symbol, callers[-1].line) if callers else None
    calls = lambda function: sum(i.is_call for i in disassemble(function))
    eligible = len(target.code) == len(candidate.code) and calls(target) == calls(candidate)
    causes = {f.cause for f in diff_code(target, candidate).findings}
    return {'schema': 'concord.frames.v1', 'unit': unit, 'symbol': symbol, 'metadata': metadata,
            'target_size': len(target.code), 'candidate_size': len(candidate.code),
            'target_calls': calls(target), 'candidate_calls': calls(candidate), 'size_and_calls_match': eligible,
            'difference_causes': sorted(c.value for c in causes),
            'frame_and_register_only': eligible and bool(causes) and causes <= {Cause.STACK_LAYOUT, Cause.REGISTER_ALLOCATION},
            'partitions': groups, 'constraints': constraints,
            'source_edit_status': 'counterfactual-required',
            'interpretation': 'Partition references identify our allocation. Aligned target slots constrain the desired position; a source edit is proven only by a canonical compile and whole-build gate.'}


def census(harvest: Harvest) -> dict:
    snapshot = scores.snapshot(scores.report(harvest.root))
    extents = dict(_extents(harvest.root))
    table = harvest.root / 'config' / BUILD / 'symbols.tsv'
    with table.open() as stream:
        known = {r['symbol']: int(r['address'], 16) for r in csv.DictReader(stream, delimiter='\t')}
    image_path = harvest.root / 'orig' / BUILD / 'Harvest'
    with image_path.open('rb') as stream:
        section = ELFFile(stream).get_section_by_name('.text')
        image, base = section.data(), section['sh_addr']
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    def calls(code):
        return sum(i.mnemonic == 'call' for i in md.disasm(code, 0))
    found = {}
    evidence_path = harvest.root / 'reference' / BUILD / 'progress' / 'evidence.json'
    evidence = json.loads(evidence_path.read_text())
    progress = harvest.root / 'build' / 'progress' / BUILD
    for report in evidence['units']:
        slug = harvest.slug(report['unit'])
        obj = progress / (slug + '.o')
        if hashlib.sha256(obj.read_bytes()).hexdigest() != report['object_sha256']:
            raise ValueError(f'stale object: {obj}')
        causal.verify_inputs(harvest.root, report['unit'], report['compilation'])
        with obj.open('rb') as stream:
            elf = ELFFile(stream)
            sections = list(elf.iter_sections())
            symbols = {s.name: s for s in elf.get_section_by_name('.symtab').iter_symbols()
                       if s['st_info']['type'] == 'STT_FUNC' and isinstance(s['st_shndx'], int)}
            for row in (f for s in report['sections'] for f in s.get('functions', []) if not f['exact']):
                address, name = int(row['address'], 16), row['symbol']
                symbol, size = symbols.get(name), extents.get(address)
                score = snapshot['functions'].get(str(address), {}).get('fuzzy', 100)
                if symbol is None or size is None or score >= 100 or symbol['st_size'] != size or known.get(name) != address:
                    continue
                ours = sections[symbol['st_shndx']].data()[symbol['st_value']:symbol['st_value'] + size]
                count = calls(ours)
                if count != calls(image[address - base:address - base + size]):
                    continue
                target_obj = progress / 'fuzzy' / (slug + '.o')
                candidate_obj = obj
                exported = read_function(candidate_obj, name)
                original = read_function(target_obj, name)
                if exported.code != ours or len(original.code) != size:
                    raise ValueError(f'stale objdiff export: {name}')
                causes = {f.cause for f in diff_code(original, exported).findings}
                category = 'frame-and-register' if Cause.STACK_LAYOUT in causes and causes <= {Cause.STACK_LAYOUT, Cause.REGISTER_ALLOCATION} else 'register-only' if causes == {Cause.REGISTER_ALLOCATION} else 'other-differences'
                item = found.setdefault(address, {'classification': category, 'causes': sorted(c.value for c in causes), 'address': hex(address), 'symbol': name, 'size': size, 'calls': count, 'fuzzy': score, 'units': []})
                item['units'].append(report['unit'])
    rows = sorted(found.values(), key=lambda r: (-r['size'], r['address']))
    groups = defaultdict(lambda: {'functions': [], 'bytes': 0})
    for row in rows:
        if row['classification'] == 'register-only':
            group = groups[row['units'][0]]
            group['functions'].append(row['symbol'])
            group['bytes'] += row['size']
    register_groups = [{'unit': unit, **group} for unit, group in sorted(groups.items(), key=lambda item: -item[1]['bytes'])]

    return {'schema': 'concord.frames-census.v1', 'image_sha256': hashlib.sha256(image_path.read_bytes()).hexdigest(),
            'distinct_addresses': len(rows), 'bytes': sum(r['size'] for r in rows), 'functions': rows,
            'frame_and_register_addresses': sum(r['classification'] == 'frame-and-register' for r in rows),
            'register_only_addresses': sum(r['classification'] == 'register-only' for r in rows),
            'register_only_bytes': sum(r['size'] for r in rows if r['classification'] == 'register-only'),
            'register_groups': register_groups,
            'symbols_sha256': hashlib.sha256(table.read_bytes()).hexdigest(),
            'evidence_sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            'interpretation': 'Same-size and same-call-count candidates need a diff before attributing them to frame layout.'}


def temporary_forms(source: bytes, symbol: str, call: str, argument: int, constructor: str) -> dict[str, bytes]:
    """Two spellings of one explicitly constructed call argument.

    The named form has a scope ending at the original full expression, so its
    destructor stays before the next statement. Other arguments must contain
    no calls or writes that would make their evaluation order observable.
    """
    from concord.transforms import _descendants, find_function

    scope = find_function(source, symbol)
    calls = [n for n in _descendants(scope, 'call_expression') if n.child_by_field_name('function').text.decode() == call]
    if len(calls) != 1:
        raise ValueError('expected one call with the requested name')
    node = calls[0]
    args = node.child_by_field_name('arguments').named_children
    if not 0 <= argument < len(args):
        raise ValueError('argument index out of range')
    arg = args[argument]
    if arg.type != 'call_expression' or arg.child_by_field_name('function').text.decode() != constructor:
        raise ValueError('argument is not the requested explicit construction')
    values = arg.child_by_field_name('arguments').named_children
    if len(values) != 1:
        raise ValueError('implicit spelling requires one constructor argument')
    implicit = source[:arg.start_byte] + values[0].text + source[arg.end_byte:]
    statement = node.parent
    while statement is not None and statement.type not in ('declaration', 'expression_statement'):
        statement = statement.parent
    if statement is None or statement.type != 'declaration' or statement.parent.type != 'compound_statement':
        raise ValueError('scoped spelling requires a local declaration')
    declarators = [n for n in statement.named_children if n.type == 'init_declarator']
    if len(declarators) != 1 or declarators[0].child_by_field_name('value') != node:
        raise ValueError('scoped spelling requires one directly initialized local')
    if statement.child_by_field_name('type').type != 'primitive_type':
        raise ValueError('scoped spelling requires a scalar result type')
    declared = declarators[0].child_by_field_name('declarator')
    if declared.type != 'identifier' or b'const' in statement.text[:declared.start_byte - statement.start_byte]:
        raise ValueError('scoped spelling requires a mutable scalar local')
    for other in [a for i, a in enumerate(args) if i != argument]:
        if other.type not in ('identifier', 'number_literal', 'string_literal', 'true', 'false', 'nullptr'):
            raise ValueError('other arguments must be simple values')
    name = b'argumentValue'
    if re.search(rb'\bargumentValue\b', source):
        raise ValueError('temporary name already exists')
    indent = source[source.rfind(b'\n', 0, statement.start_byte) + 1:statement.start_byte]
    if indent.strip():
        raise ValueError('declaration must begin on its own line')
    expression = source[node.start_byte:arg.start_byte] + name + source[arg.end_byte:node.end_byte]
    declaration = statement.text[:declared.end_byte - statement.start_byte] + b';'
    text = (declaration + b'\n' + indent + b'{\n' + indent + b'    ' + constructor.encode() + b' ' + name + arg.child_by_field_name('arguments').text + b';\n'
            + indent + b'    ' + declared.text + b' = ' + expression + b';\n' + indent + b'}')
    scoped = source[:statement.start_byte] + text + source[statement.end_byte:]
    return {'implicit-construction': implicit, 'scoped-construction': scoped}


def probe_temporary(harvest: Harvest, unit: str, symbol: str, call: str, argument: int, constructor: str, apply: bool = False) -> dict:
    path = harvest.root / 'src' / unit
    source = path.read_bytes()
    forms = temporary_forms(source, symbol, call, argument, constructor)
    evaluations = harvest.evaluate(unit, {'baseline': source, **forms})
    baseline = evaluations['baseline']
    if baseline.error:
        raise ValueError('baseline does not compile: ' + baseline.error)
    results, accepted = {}, []
    for name, candidate in forms.items():
        row = evaluations[name]
        worse = {s: [v, row.fuzzy[s]] for s, v in baseline.fuzzy.items() if s in row.fuzzy and row.fuzzy[s] < v}
        exact = symbol in row.exact_functions()
        results[name] = {'object': str(row.object), 'error': row.error, 'function_exact': exact, 'worse': worse,
                         'source_sha256': hashlib.sha256(candidate).hexdigest(),
                         'before_score': baseline.fuzzy.get(symbol), 'after_score': row.fuzzy.get(symbol),
                         'baseline_object': str(baseline.object)}
        if not row.error and exact and not worse:
            accepted.append(name)
    chosen = accepted[0] if accepted else None
    if chosen and apply:
        if path.read_bytes() != source:
            raise ValueError('source changed during temporary probe')
        path.write_bytes(forms[chosen])
    return {'unit': unit, 'symbol': symbol, 'call': call, 'argument': argument, 'constructor': constructor,
            'source_sha256': hashlib.sha256(source).hexdigest(), 'variants': results, 'chosen': chosen,
            'applied': bool(chosen and apply), 'whole_build_gate_required': True}
