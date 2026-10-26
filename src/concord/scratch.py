"""Actual peephole scratch searches, including invisible failed attempts.

RTL dumps contain successful rewrites, not the complete search history. The
collector observes the pinned compiler and rejects a changed canonical object.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from concord import causal
from concord.harvest import Harvest

SCHEMA = 'concord.scratch-history.v1'


def validate_events(events: list[dict], count: int) -> None:
    position = 0
    for index, event in enumerate(events):
        order = event['allocation_order']
        if event['index'] != index or event['before'] != position:
            raise ValueError(f'discontinuous scratch history at event {index}')
        if len(order) != count or sorted(order) != list(range(count)):
            raise ValueError('invalid hard-register allocation order')
        position = event['after']
        if not 0 <= position < count or not event['function'] or event['uid'] <= 0:
            raise ValueError('invalid scratch event')
        if event['success']:
            if event['register'] != order[(position - 1) % count]:
                raise ValueError('successful search does not advance past its register')
        elif position != 0 or event['register'] is not None:
            raise ValueError('failed search does not reset its position')


def collect(harvest: Harvest, unit: str) -> Path:
    unit = harvest.source(unit)
    out = harvest.root / 'build' / 'concord' / 'scratch' / uuid.uuid4().hex
    done = subprocess.run(['uv', '--no-config', 'run', '--locked', 'python',
                           str(Path(__file__).with_name('scratch_driver.py')), unit, str(out)],
                          cwd=harvest.root, capture_output=True, text=True, check=False)
    if done.returncode:
        message = done.stderr[-3000:]
        if (out / 'debugger.log').is_file():
            message += '\n' + (out / 'debugger.log').read_text(errors='replace')[-3000:]
        raise RuntimeError('scratch collection failed:\n' + message)
    json.loads(done.stdout)
    return out


def load(root: Path, unit: str, directory: Path) -> tuple[dict, list[dict]]:
    metadata = json.loads((directory / 'metadata.json').read_text())
    causal.verify_inputs(root, unit, metadata)
    for name, digest in metadata['artifacts'].items():
        path = directory / name
        if path.parent != directory or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('scratch artifact changed: ' + name)
    if not metadata.get('diagnostic_object_verified'):
        raise ValueError('canonical object was not verified')
    trace = json.loads((directory / 'events.json').read_text())
    if trace['errors'] or trace['exit_code'] != 0:
        raise ValueError('incomplete scratch trace')
    validate_events(trace['events'], metadata['profile']['register_count'])
    return metadata, trace['events']


def _records(directory: Path, symbols: set[str]) -> dict:
    found = defaultdict(dict)
    # The last pass before peephole2 in the pin is csa. Generated instructions
    # searched again within peephole2 can instead be present only in its output.
    for suffix in ('181r.csa', '182r.peephole2'):
        text = next(directory.glob('*.' + suffix)).read_text(errors='replace')
        headers = list(causal.HEAD.finditer(text))
        for index, header in enumerate(headers):
            names = set(re.findall(r'\(([^ ,()]+)(?:[, )])', header[1])) & symbols
            if len(names) != 1:
                continue
            symbol = names.pop()
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            records = causal.rtl_function(text[header.start():end], symbol)
            for uid, row in records.items():
                found[symbol].setdefault(uid, row)
    return found


def summarize(events: list[dict], selected: set[str]) -> dict:
    targets, counts, last_reset = [], defaultdict(Counter), None
    for event in events:
        name = event['function']
        counts[name]['success' if event['success'] else 'failure'] += 1
        if name in selected:
            targets.append({'event': event['index'], 'function': name, 'uid': event['uid'],
                            'before': event['before'], 'after': event['after'],
                            'success': event['success'], 'register': event['register_name'],
                            'last_reset': last_reset,
                            'history_range': [last_reset if last_reset is not None else 0, event['index']],
                            'source': event.get('source'), 'input_pattern': event.get('input_pattern')})
        if not event['success']:
            last_reset = event['index']
    return {'searches': len(events), 'failed_searches': sum(not e['success'] for e in events),
            'functions': [{'function': name, **count} for name, count in counts.items()], 'targets': targets}


def compare(current: dict, reference: dict) -> list[dict]:
    before, after = _group(reference['events']), _group(current['events'])
    rows = []
    exact = set(reference['metadata']['matched_functions'])
    for symbol in set(before) & set(after):
        a, b = before[symbol], after[symbol]
        if len(a) != len(b):
            continue
        for index, (left, right) in enumerate(zip(a, b, strict=True)):
            if any(left[key] != right[key] for key in ('class', 'mode', 'from', 'to', 'reserved', 'allocation_order')):
                continue
            if left['before'] == right['before'] and left['register'] == right['register']:
                continue
            rows.append({'function': symbol, 'ordinal': index, 'reference_event': left['index'],
                         'current_event': right['index'], 'reference_before': left['before'],
                         'current_before': right['before'], 'reference_register': left['register_name'],
                         'current_register': right['register_name'],
                         'reference_function_exact': symbol in exact,
                         'reference_predecessor': _predecessor(reference['events'], left['index']),
                         'current_predecessor': _predecessor(current['events'], right['index']),
                         'history_delta': _history_delta(reference['events'], left['index'],
                                                         current['events'], right['index']),
                         'status': 'ordinal-reference-constraint',
                         'source_edit_status': 'counterfactual-required'})
    return sorted(rows, key=lambda r: r['current_event'])


def _predecessor(events: list[dict], index: int) -> dict | None:
    if not index:
        return None
    event = events[index - 1]
    return {key: event.get(key) for key in ('index', 'function', 'uid', 'before', 'after', 'register_name', 'source')}


def _history_delta(before: list[dict], stop_before: int, after: list[dict], stop_after: int) -> dict:
    def prefix(events, stop):
        reset = next((i for i in range(stop - 1, -1, -1) if not events[i]['success']), -1)
        return events[reset + 1:stop]
    left, right = prefix(before, stop_before), prefix(after, stop_after)
    counts_left, counts_right = Counter(e['function'] for e in left), Counter(e['function'] for e in right)
    return {'reference_sequence': [e['index'] for e in left],
            'current_sequence': [e['index'] for e in right],
            'changed_search_counts': [
                {'function': name, 'reference': counts_left[name], 'current': counts_right[name]}
                for name in sorted(counts_left.keys() | counts_right.keys())
                if counts_left[name] != counts_right[name]],
            'constraint': 'Restore the reference incoming position and register availability; count alone is insufficient.'}


def _group(events):
    groups = defaultdict(list)
    for event in events:
        groups[event['function']].append(event)
    return groups


def report(harvest: Harvest, unit: str, directory: Path, symbols: list[str], reference: Path | None = None) -> dict:
    unit = harvest.source(unit)
    metadata, events = load(harvest.root, unit, directory)
    records = _records(directory, {event['function'] for event in events})
    for event in events:
        row = records.get(event['function'], {}).get(event['uid'])
        event['source'] = causal.source_construct(harvest.root, row['source']) if row else None
        event['input_pattern'] = row['pattern'] if row else None
    selected = set(symbols) if symbols else {event['function'] for event in events}
    result = {'schema': SCHEMA, 'unit': unit, 'directory': str(directory), 'metadata': metadata,
              'observability': 'complete-execution-search-trace', 'events': events,
              **summarize(events, selected)}
    if reference:
        old = json.loads(reference.read_text())
        if (old['schema'] != SCHEMA or old['unit'] != unit
                or not old['metadata'].get('diagnostic_object_verified')):
            raise ValueError('incompatible scratch reference')
        for key in ('container_image_id', 'compiler_version', 'package_manifest_sha256', 'runtime_sha256'):
            if old['metadata'][key] != metadata[key]:
                raise ValueError('reference compiler changed: ' + key)
        validate_events(old['events'], metadata['profile']['register_count'])
        result['reference_sha256'] = hashlib.sha256(reference.read_bytes()).hexdigest()
        result['reference_differences'] = compare(result, old)
    return result
