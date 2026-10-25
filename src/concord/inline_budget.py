"""Explain GCC's observed inlining budget and the edges that consume it."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

CONSIDER = re.compile(r'^Considering (.+?)(?: with)? (\d+) insns(?: \(always inline\))?$')
GRAPH = re.compile(r'^(.+)/(\d+)\(-?\d+\): (.*?)(\d+) insns(?: \((\d+) after inlining\))?')


def parse(text: str) -> dict:
    start = re.search(r'Deciding on inlining\.  Starting with (\d+) insns\.', text)
    summary = re.search(r'Inlined (\d+) calls, eliminated (\d+) functions, (\d+) insns turned to (\d+) insns\.', text)
    if not start or not summary:
        raise ValueError('expected a complete GCC IPA inline decision summary')
    events, functions, current = [], {}, None
    decision_end = text[:summary.start()].count('\n') + 1
    for number, line in enumerate(text.splitlines(), 1):
        if number >= decision_end:
            current = None
        if number < decision_end and (match := CONSIDER.match(line)):
            current = {'callee': match[1], 'callee_insns': int(match[2]), 'line': number}
            events.append(current)
        elif current is not None:
            if line.startswith(' to be inlined into '):
                current['caller'] = line.removeprefix(' to be inlined into ')
            elif match := re.search(r'Estimated badness is (-?\d+), frequency (\d+(?:\.\d+)?)', line):
                current.update(badness=int(match[1]), frequency=float(match[2]))
            elif match := re.search(r'net change of ([+-]?\d+) insns', line):
                current.update(outcome='inlined', net_change=int(match[1]))
            elif 'inline_failed:' in line:
                current.update(outcome='rejected', reason=line.split('inline_failed:', 1)[1].strip())
        if (match := GRAPH.match(line)) and 'inline copy in ' not in match[3]:
            functions[match[1]] = {'name': match[1], 'initial_insns': int(match[4]),
                                       'final_insns': int(match[5] or match[4])}
    rejected = Counter(e.get('reason') for e in events if e.get('outcome') == 'rejected')
    return {'schema': 'concord.inline-budget.v1', 'pass': 'pass_ipa_inline',
            'starting_insns': int(start[1]), 'final_insns': int(summary[4]),
            'inlined_calls': int(summary[1]), 'eliminated_functions': int(summary[2]),
            'rejections': dict(rejected), 'events': events,
            'functions': sorted(functions.values(), key=lambda r: (-r['initial_insns'], r['name']))}


def compare(before: dict, after: dict) -> dict:
    """Separate new function estimates from changed call-edge decisions.

    Function estimates are compiler units, not target bytes. Their sum need
    not equal the unit total because GCC accounts for removed functions and
    inline clones separately.
    """
    old = {r['name']: r for r in before['functions']}
    new = {r['name']: r for r in after['functions']}
    functions = []
    for name in old.keys() | new.keys():
        a, b = old.get(name, {}).get('initial_insns', 0), new.get(name, {}).get('initial_insns', 0)
        if a != b:
            functions.append({'name': name, 'before_insns': a, 'after_insns': b, 'delta': b - a})
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
