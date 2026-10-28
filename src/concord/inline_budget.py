"""Explain GCC's observed inlining budget and the edges that consume it."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from pathlib import Path

CONSIDER = re.compile(r'^Considering (.+?)(?: with)? (\d+) insns(?: \(always inline\))?\.?$')
GRAPH = re.compile(r'^(.+)/(\d+)\(-?\d+\): (.*?)(\d+) insns(?: \((\d+) after inlining\))?')


def parse(text: str) -> dict:
    start = re.search(r'Deciding on inlining\.  Starting with (\d+) insns\.', text)
    summary = re.search(r'Inlined (\d+) calls, eliminated (\d+) functions, (\d+) insns turned to (\d+) insns\.', text)
    if not start or not summary:
        raise ValueError('expected a complete GCC IPA inline decision summary')
    events, functions, current = [], {}, None
    phase = None
    decision_end = text[:summary.start()].count('\n') + 1
    for number, line in enumerate(text.splitlines(), 1):
        if number >= decision_end:
            current = None
        if line.startswith('Deciding on smaller functions:'):
            phase = 'smaller-functions'
        elif line.startswith('Deciding on functions called once:'):
            phase = 'called-once'
        elif line.startswith('Inlining always_inline functions:'):
            phase = 'always-inline'
        if number < decision_end and (match := CONSIDER.match(line)):
            current = {'callee': match[1], 'callee_insns': int(match[2]), 'line': number,
                       'order': len(events) + 1, 'phase': phase}
            events.append(current)
        elif number < decision_end and line.startswith('Considering inline candidate '):
            current = {'callee': line.removeprefix('Considering inline candidate ').removesuffix('.'),
                       'line': number, 'order': len(events) + 1, 'phase': phase}
            events.append(current)
        elif current is not None:
            if line.startswith(' to be inlined into '):
                current['caller'] = line.removeprefix(' to be inlined into ')
            elif match := re.match(r' Called once from (.+) (\d+) insns\.', line):
                current.update(caller=match[1], caller_insns_before=int(match[2]))
            elif match := re.search(r'Estimated growth after inlined into all callees is ([+-]?\d+) insns', line):
                current['growth_all_calls'] = int(match[1])
            elif match := re.search(r'Estimated badness is (-?\d+), frequency (\d+(?:\.\d+)?)', line):
                current.update(badness=int(match[1]), frequency=float(match[2]))
            elif match := re.search(r'net change of ([+-]?\d+) insns', line):
                current.update(outcome='inlined', net_change=int(match[1]))
                if caller := re.search(r'which now has (\d+) insns', line):
                    current['caller_insns_after'] = int(caller[1])
            elif 'inline_failed:' in line:
                current.update(outcome='rejected', reason=line.split('inline_failed:', 1)[1].strip())
            elif line.startswith('Inlining failed: '):
                current.update(outcome='rejected', reason=line.removeprefix('Inlining failed: '))
            elif 'Inline limit reached, not inlined.' in line:
                current.update(outcome='rejected', reason='inline limit reached')
        if (match := GRAPH.match(line)) and 'inline copy in ' not in match[3]:
            functions[match[1], int(match[2])] = {'name': match[1], 'node_id': int(match[2]), 'initial_insns': int(match[4]),
                                       'final_insns': int(match[5] or match[4])}
    # The net unit change can include removal of an out-of-line function.
    # Use caller sizes, rather than that net change, for a single edge's growth.
    names = Counter(row['name'] for row in functions.values())
    sizes = {row['name']: row['initial_insns'] for row in functions.values() if names[row['name']] == 1}
    for event in events:
        caller = event.get('caller')
        if 'caller_insns_after' in event:
            before = event.get('caller_insns_before', sizes.get(caller))
            if before is not None:
                event['caller_insns_before'] = before
                event['edge_growth'] = event['caller_insns_after'] - before
            if names[caller] == 1:
                sizes[caller] = event['caller_insns_after']
    rejected = Counter(e.get('reason') for e in events if e.get('outcome') == 'rejected')
    return {'schema': 'concord.inline-budget.v1', 'pass': 'pass_ipa_inline',
            'starting_insns': int(start[1]), 'final_insns': int(summary[4]),
            'inlined_calls': int(summary[1]), 'eliminated_functions': int(summary[2]),
            'rejections': dict(rejected), 'events': events,
            'functions': sorted(functions.values(), key=lambda r: (-r['initial_insns'], r['name']))}


def literal_sites(source: bytes, ours, target, ours_sections: dict, target_sections: dict) -> list[dict]:
    """CString literal constructions with raw reference offsets on both sides.

    One reference denotes an out-of-line construction and three denote the
    inlined pointer constructor in this pinned build. Repeated literals and
    other counts stay unresolved, since counts cannot identify their sites.
    """
    from concord.calls_constants import _pooled_literal
    from concord.transforms import CPP, Parser

    parser = Parser(CPP)
    tree = parser.parse(source)
    literals, pending = [], [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == 'string_literal':
            literals.append(node)
        pending.extend(reversed(node.named_children))
    counts = Counter(n.text for n in literals)
    references = []
    for body, sections in ((ours, ours_sections), (target, target_sections)):
        found = defaultdict(list)
        for ref in body.datarefs:
            literal = _pooled_literal(ref, sections)
            if literal:
                found[literal].append(ref.offset)
        references.append(found)
    rows = []
    for node in literals:
        call = node.parent
        if call is not None and call.type == 'argument_list':
            call = call.parent
        else:
            continue
        function = call.child_by_field_name('function') if call else None
        if call is not None and call.type == 'init_declarator' and call.parent.type == 'declaration':
            function = call.parent.child_by_field_name('type')
        if function is None or not re.search(rb'\bCString\s*<\s*(?:char|wchar_t)\s*>$', function.text):
            continue
        raw = node.text
        # Escaped and concatenated literals need C++ evaluation; do not
        # guess their byte values or the compiler's reference multiplicity.
        if not raw.startswith((b'"', b'L"')) or b'\\' in raw:
            continue
        encoding = 'utf32le' if raw.startswith(b'L') else 'utf8'
        value = raw[2:-1] if encoding == 'utf32le' else raw[1:-1]
        label = value.decode('utf8', 'backslashreplace')
        value = label.encode('utf-32le') if encoding == 'utf32le' else value
        row = {'literal': label, 'encoding': encoding, 'source_line': source.count(b'\n', 0, node.start_byte) + 1,
               'source_occurrences': counts[raw]}
        for side, found in zip(('ours', 'target'), references, strict=True):
            offsets = found[encoding, value]
            decision = {1: 'out-of-line', 3: 'inlined'}.get(len(offsets), 'unresolved') if counts[raw] == 1 else 'unresolved'
            row[side] = {'reference_count': len(offsets), 'offsets': offsets, 'decision': decision}
        rows.append(row)
    return rows


def sites(harvest, unit: str, symbols: list[str]) -> list[dict]:
    from concord.calls_constants import read_refs, read_string_sections
    from concord.transforms import find_function

    unit = harvest.source(unit)
    slug = harvest.slug(unit)
    ours_path = harvest.reports / f'{slug}.o'
    target_path = harvest.objects / 'target' / f'{slug}.o'
    ours, target = read_refs(ours_path, set(symbols)), read_refs(target_path, set(symbols))
    ours_sections, target_sections = read_string_sections(ours_path), read_string_sections(target_path)
    source = (harvest.root / 'src' / unit).read_bytes()
    rows = []
    for symbol in symbols:
        scope = find_function(source, symbol)
        first_line = source.count(b'\n', 0, scope.start_byte)
        literals = literal_sites(scope.text, ours[symbol], target[symbol], ours_sections, target_sections)
        for row in literals:
            row['source_line'] += first_line
        rows.append({'unit': unit, 'symbol': symbol, 'source_sha256': hashlib.sha256(source).hexdigest(),
                     'ours_object_sha256': hashlib.sha256(ours_path.read_bytes()).hexdigest(),
                     'target_object_sha256': hashlib.sha256(target_path.read_bytes()).hexdigest(),
                     'sites': literals})
    return rows


def verify_dump(harvest, unit: str, symbols: list[str], path: Path) -> dict:
    """Reject stale diagnostics and confirm the traced function's canonical bytes."""
    import json

    from concord.causal import verify_inputs
    from concord.diff import read_functions

    metadata = json.loads((path.parent / 'metadata.json').read_text())
    verify_inputs(harvest.root, harvest.source(unit), metadata)
    diagnostic = path.parent / 'trace.o'
    canonical = harvest.reports / f'{harvest.slug(unit)}.o'
    left, right = read_functions(diagnostic, symbols), read_functions(canonical, symbols)
    for symbol in symbols:
        a, b = left[symbol], right[symbol]
        if a.code != b.code or a.relocations != b.relocations:
            raise ValueError(f'diagnostic function differs from the canonical compile: {symbol}')
    if metadata.get('artifacts', {}).get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('inline dump changed after collection')
    return {'inputs_verified': True, 'function_bytes_and_relocation_offsets_verified': True,
            'diagnostic_object_sha256': hashlib.sha256(diagnostic.read_bytes()).hexdigest()}


def budgets(report: dict, params: dict[str, int]) -> dict:
    """Apply GCC 4.4's integer budget formulas to observed estimates.

    Parameters must come from the compiler's params.def and command line.
    A growth cap permits the last edge to cross it before the next heap pop.
    """
    size = report['starting_insns']
    greedy_start = None
    minimum = None
    trace = []
    estimates = defaultdict(set)
    for row in report['functions']:
        estimates[row['name']].add(row['initial_insns'])
    functions = {name: next(iter(values)) for name, values in estimates.items() if len(values) == 1}
    for event in report['events']:
        if event['phase'] == 'smaller-functions' and greedy_start is None:
            greedy_start = minimum = size
        if 'net_change' not in event:
            continue
        before = size
        size += event['net_change']
        row = {'order': event['order'], 'unit_insns_before': before, 'unit_insns_after': size}
        if event['phase'] == 'smaller-functions':
            row['unit_limit_before'] = max(minimum, params['large-unit-insns']) * (100 + params['inline-unit-growth']) // 100
            minimum = min(minimum, size)
        caller = functions.get(event.get('caller'))
        callee = functions.get(event['callee'])
        if caller is not None and callee is not None:
            base = max(caller, callee)
            row['function_growth_limit'] = max(params['large-function-insns'], base + base * params['large-function-growth'] // 100)
        trace.append(row)
    complete = size == report['final_insns']
    limit = max(minimum, params['large-unit-insns']) * (100 + params['inline-unit-growth']) // 100 if minimum is not None else None
    return {'params': params, 'greedy_starting_insns': greedy_start,
            'minimum_insns': minimum,
            'unit_limit': limit if complete else None, 'accounted_unit_limit': limit,
            'accounted_final_insns': size, 'complete_net_change_trace': complete,
            'events': trace,
            'site_order': 'The dump orders caller/callee decisions but does not print edge IDs or literal arguments; literal sites cannot be assigned a heap-pop order from this dump.'}


def parameters(path: Path, command: list[str] = ()) -> dict:
    text = path.read_text()
    values = {name: int(value) for name, value in re.findall(
        r'DEFPARAM\([^,]+,\s*"([^"]+)",\s*"[^"]*",\s*(\d+)', text)}
    for index, argument in enumerate(command):
        setting = command[index + 1] if argument == '--param' else argument.removeprefix('--param=') if argument.startswith('--param=') else ''
        if '=' in setting:
            name, value = setting.split('=', 1)
            if name in values:
                values[name] = int(value)
    wanted = ('large-unit-insns', 'inline-unit-growth', 'large-function-insns', 'large-function-growth', 'inline-call-cost')
    return {name: values[name] for name in wanted}


def construction_forms(source: bytes, symbol: str, literals: set[str]) -> dict[str, bytes]:
    """Name one literal construction with its original statement lifetime.

    Limit the probe to standalone expression statements containing exactly
    one CString construction. A surrounding scope ends the named value's
    lifetime where the original full-expression temporary was destroyed.
    """
    from concord.transforms import _descendants, find_function

    scope = find_function(source, symbol)
    forms = {}
    for call in _descendants(scope, 'call_expression'):
        function = call.child_by_field_name('function')
        arguments = call.child_by_field_name('arguments')
        if function is None or arguments is None or not re.search(rb'\bCString\s*<\s*(?:char|wchar_t)\s*>$', function.text):
            continue
        args = arguments.named_children
        if len(args) != 1 or args[0].type != 'string_literal' or args[0].text.removeprefix(b'L')[1:-1].decode(errors='replace') not in literals:
            continue
        statement = call.parent
        while statement and statement.type not in ('expression_statement', 'declaration', 'compound_statement'):
            statement = statement.parent
        if statement is None or statement.type != 'expression_statement' or statement.parent.type != 'compound_statement':
            continue
        # Reject another constructor or a name collision in this scope.
        if statement.text.count(b'CString') != 1 or b'spriteName' in scope.text:
            continue
        start = source.rfind(b'\n', 0, statement.start_byte) + 1
        indent = source[start:statement.start_byte]
        if indent.strip():
            continue
        relative = call.start_byte - statement.start_byte
        expression = statement.text[:relative] + b'spriteName' + statement.text[relative + len(call.text):]
        replacement = (b'{\n' + indent + b'    ' + function.text + b' spriteName' + arguments.text + b';\n'
                       + indent + b'    ' + expression + b'\n' + indent + b'}')
        line = source.count(b'\n', 0, call.start_byte) + 1
        name = f'named-construction-{line}'
        forms[name] = source[:statement.start_byte] + replacement + source[statement.end_byte:]
    return forms


def probe(harvest, unit: str, symbol: str, *, apply: bool = False) -> dict:
    """Screen per-site construction forms as overlays; author gates a winner."""
    from concord import authored
    from concord.calls_constants import read_refs, read_string_sections
    from concord.census import delta
    from concord.transforms import find_function

    unit = harvest.source(unit)
    source = (harvest.root / 'src' / unit).read_bytes()
    [function] = sites(harvest, unit, [symbol])
    literals = {row['literal'] for row in function['sites']
                if row['ours']['decision'] != row['target']['decision']
                and 'unresolved' not in (row['ours']['decision'], row['target']['decision'])}
    forms = construction_forms(source, symbol, literals)
    evaluations = harvest.evaluate_overlays({'baseline': (unit, {}), **{
        name: (unit, {f'src/{unit}': text}) for name, text in forms.items()}})
    baseline = evaluations.pop('baseline')
    if baseline.error:
        raise ValueError(baseline.error)
    target_path = harvest.objects / 'target' / f'{harvest.slug(unit)}.o'
    target = read_refs(target_path, {symbol})[symbol]
    target_sections = read_string_sections(target_path)
    original_sites = {r['literal']: r for r in function['sites']}
    candidates = {}
    for name, row in evaluations.items():
        candidates[name] = {'error': row.error, 'before_score': baseline.fuzzy.get(symbol),
                            'score': row.fuzzy.get(symbol), 'object': str(row.object),
                            'source_sha256': hashlib.sha256(forms[name]).hexdigest(),
                            'delta': delta(baseline, row) if not row.error else None,
                            'eligible': authored.keep_improvement(baseline, row, symbol)}
        if not row.error:
            measured = literal_sites(find_function(forms[name], symbol).text,
                                     read_refs(row.object, {symbol})[symbol], target,
                                     read_string_sections(row.object), target_sections)
            candidates[name]['site_decision_changes'] = [
                {'literal': r['literal'], 'before': original_sites[r['literal']]['ours']['decision'],
                 'after': r['ours']['decision'], 'target': r['target']['decision']}
                for r in measured if r['literal'] in original_sites
                and r['ours']['decision'] != original_sites[r['literal']]['ours']['decision']]
    winner = max((name for name, row in candidates.items() if row['eligible']),
                 key=lambda name: candidates[name]['score'], default=None)
    result = {'unit': unit, 'symbol': symbol, 'candidates': candidates, 'chosen': winner, 'applied': False}
    if winner and apply:
        report = authored.apply(harvest, unit, symbol, write=True, candidate=forms[winner])
        result['author'] = {key: value for key, value in report.items() if key not in ('original', 'source')}
        result['applied'] = report['applied']
    return result


def compare(before: dict, after: dict) -> dict:
    """Separate new function estimates from changed call-edge decisions.

    Function estimates are compiler units, not target bytes. Their sum need
    not equal the unit total because GCC accounts for removed functions and
    inline clones separately.
    """
    def estimates(report):
        rows = defaultdict(list)
        for row in report['functions']:
            rows[row['name']].append(row['initial_insns'])
        return {name: sorted(values) for name, values in rows.items()}

    old, new = estimates(before), estimates(after)
    functions = []
    for name in old.keys() | new.keys():
        a, b = sum(old.get(name, [])), sum(new.get(name, []))
        if old.get(name, []) != new.get(name, []):
            functions.append({'name': name, 'before_insns': a, 'after_insns': b, 'delta': b - a,
                              'before_estimates': old.get(name, []), 'after_estimates': new.get(name, [])})
    def edges(report):
        return Counter((e['callee'], e.get('caller', '')) for e in report['events'] if e.get('outcome') == 'inlined')
    a, b = edges(before), edges(after)
    return {'starting_insns_delta': after['starting_insns'] - before['starting_insns'],
            'final_insns_delta': after['final_insns'] - before['final_insns'],
            'functions': sorted(functions, key=lambda r: (-abs(r['delta']), r['name'])),
            'inline_edge_deltas': [{'callee': callee, 'caller': caller, 'before': a[callee, caller], 'after': b[callee, caller]}
                                   for callee, caller in sorted(a.keys() | b.keys()) if a[callee, caller] != b[callee, caller]]}


def load(path: Path) -> dict:
    data = path.read_bytes()
    return {**parse(data.decode(errors='replace')), 'dump': str(path.resolve()), 'sha256': hashlib.sha256(data).hexdigest()}
