"""Rank traced source constructs by distinct target functions and their bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path


def select(progress: dict, minimum: float = 97, excluded: tuple[str, ...] = ('daisy/gui/',)) -> list[dict]:
    """Select authoritative target extents, including unproven 100% scores."""
    found = {}
    for item in progress['units']:
        source = item.get('metadata', {}).get('source_path', '')
        if not source.startswith('src/') or int(item['measures']['matched_code']) == int(item['measures']['total_code']):
            continue
        unit = source.removeprefix('src/')
        if unit.startswith(excluded):
            continue
        for function in item.get('functions', []):
            score = function.get('fuzzy_match_percent', 0)
            if score < minimum:
                continue
            address = int(function['metadata']['virtual_address'])
            found[address] = {'unit': unit, 'symbol': function['name'], 'address': address,
                              'size': int(function['size']), 'fuzzy': score}
    return sorted(found.values(), key=lambda f: (-f['size'], f['address']))


def source_path(root: Path, path: str) -> str:
    if path.startswith('/work/'):
        path = path.removeprefix('/work/')
    normalized = Path(os.path.normpath(path))
    if normalized.is_absolute() and normalized.is_relative_to(root):
        normalized = normalized.relative_to(root)
    return str(normalized)


def rank(root: Path, reports: list[dict]) -> dict:
    """One function's size counts once per construct, regardless of site count."""
    constructs = {}
    selected, traced, unresolved = set(), set(), set()
    for report in reports:
        function = report['function']
        address = function['address']
        selected.add(address)
        for finding in report.get('findings', []):
            site = (address, finding['target_offset'])
            source = finding.get('source')
            if finding.get('status') != 'observed-candidate-decision' or not source:
                unresolved.add(site)
                continue
            traced.add(site)
            path = source_path(root, source['path'])
            context = finding.get('context', {})
            classification = context.get('classification', 'source')
            original = {'path': path, 'line': source['line'], **context}
            if classification == 'caller-context':
                path = 'src/' + function['unit']
                key = (path, function['symbol'])
                line, text = 0, function['symbol']
            else:
                key = (path, source['line'])
                line, text = source['line'], source.get('text', '')
            row = constructs.setdefault(key, {'path': path, 'line': line, 'text': text, 'classification': classification, 'header_evidence': [],
                                               'functions': {}, 'sites': set(), 'passes': set(), 'causes': set()})
            if context and original not in row['header_evidence']:
                row['header_evidence'].append(original)
            row['functions'][address] = function
            row['sites'].add(site)
            row['causes'].add(finding['cause'])
            if finding.get('decision'):
                row['passes'].add(finding['decision']['after']['pass'])
    rows = []
    for row in constructs.values():
        functions = sorted(row.pop('functions').values(), key=lambda f: (-f['size'], f['address']))
        rows.append({**row, 'functions': functions, 'function_count': len(functions),
                     'function_bytes': sum(f['size'] for f in functions), 'sites': len(row['sites']),
                     'passes': sorted(row['passes']), 'causes': sorted(row['causes']),
                     'header': Path(row['path']).suffix in ('.h', '.hpp', '.tcc')})
    rows.sort(key=lambda r: (-r['function_count'], -r['function_bytes'], r['path'], r['line']))
    return {'schema': 'concord.causal-census.v1', 'selected_functions': len(selected),
            'traced_sites': len(traced), 'unresolved_sites': len(unresolved - traced),
            'constructs': [r for r in rows if r['classification'] != 'header-unverified'],
            'caller_context': [r for r in rows if r['classification'] == 'caller-context'],
            'header_candidates': [r for r in rows if r['classification'] == 'header-candidate'],
            'unverified_headers': [r for r in rows if r['classification'] == 'header-unverified']}


def groups(functions: list[dict]) -> dict[str, list[dict]]:
    units = defaultdict(list)
    for function in functions:
        units[function['unit']].append(function)
    return dict(units)


def run(harvest, save: Path, minimum: float = 97, maximum: int | None = None, reuse: Path | None = None) -> dict:
    from concord import causal, scores

    functions = select(scores.report(harvest.root), minimum)
    cached = {u['unit']: Path(u['dumps']) for u in json.loads(reuse.read_text())['units']} if reuse else {}
    if maximum is not None:
        functions = functions[:maximum]
    directory = save.parent / (save.stem + '-units')
    directory.mkdir(parents=True, exist_ok=True)
    reports, failures, details = [], [], []
    result = {**rank(harvest.root, []), 'minimum_score': minimum, 'selected_functions': len(functions),
              'analyzed_functions': 0, 'failures': failures, 'units': details}
    save.write_text(json.dumps(result, indent=2) + '\n')
    by_unit = groups(functions)
    for number, (unit, selected) in enumerate(by_unit.items(), 1):
        print(f'{number}/{len(by_unit)} {unit}: {len(selected)} functions', flush=True)
        try:
            dumps = cached.get(unit)
            if dumps:
                try:
                    causal.verify_inputs(harvest.root, unit, json.loads((dumps / 'metadata.json').read_text()))
                except (OSError, ValueError):
                    dumps = None
            dumps = dumps or causal.collect(harvest, unit)
            try:
                unit_reports = causal.analyze_many(harvest, unit, [f['symbol'] for f in selected], dumps)
            except (ValueError, KeyError, StopIteration):
                unit_reports = []
                for function in selected:
                    try:
                        unit_reports.append(causal.analyze(harvest, unit, function['symbol'], dumps, limit=None))
                    except (ValueError, KeyError, StopIteration) as error:
                        failures.append({'function': function, 'error': str(error)})
            indexed = {f['symbol']: f for f in selected}
            for report in unit_reports:
                report['function'] = indexed[report['symbol']]
            path = directory / (harvest.slug(unit) + '.json')
            path.write_text(json.dumps(unit_reports, indent=2) + '\n')
            details.append({'unit': unit, 'dumps': str(dumps), 'report': str(path)})
            reports.extend(unit_reports)
        except (OSError, RuntimeError, ValueError) as error:
            failures.extend({'function': f, 'error': str(error)} for f in selected)
        result = {**rank(harvest.root, reports), 'minimum_score': minimum, 'selected_functions': len(functions),
                  'analyzed_functions': len(reports), 'failures': failures, 'units': details}
        save.write_text(json.dumps(result, indent=2) + '\n')
    from concord.copy_context import annotate, copy_index, header_symbols

    owners = defaultdict(set)
    paths = {source_path(harvest.root, f['source']['path']) for report in reports for f in report['findings']
             if f.get('source') and Path(f['source']['path']).suffix in ('.h', '.hpp', '.tcc')}
    for detail in details:
        for key, names in header_symbols(harvest.root, Path(detail['dumps']) / 'trace.o', paths).items():
            owners[key].update(names)
    annotate(harvest.root, reports, owners, copy_index(harvest.reports))
    for detail in details:
        Path(detail['report']).write_text(json.dumps([r for r in reports if r['unit'] == detail['unit']], indent=2) + '\n')
    result.update(rank(harvest.root, reports), selected_functions=len(functions))
    save.write_text(json.dumps(result, indent=2) + '\n')
    return result


def rewrites(source: bytes, line: int) -> list:
    from tree_sitter import Parser

    from concord.transforms import CPP, swap_operands, while_loop

    root = Parser(CPP).parse(source).root_node
    return [rewrite for transform in (swap_operands, while_loop) for rewrite in transform(source, root)
            if int(re.match(r'line (\d+):', rewrite.description)[1]) == line]


def authored_rewrite(source: bytes, header: str, unit: str, symbol: str, edit=None):
    """Find a registered header correction and require its anchor and path."""
    from concord.authored import HEADER_FUNCTION_AUTHORED, header_function_candidate
    from concord.model import Cause
    from concord.transforms import Rewrite

    edit = edit or HEADER_FUNCTION_AUTHORED.get((unit, symbol))
    if edit is None or f'src/{edit.header}' != header:
        raise ValueError(f'no authored correction for {unit} {symbol} in {header}')
    candidate = header_function_candidate(source, symbol, edit)
    if candidate is None:
        raise ValueError(f'authored correction {edit.name} does not apply to {header}')
    return Rewrite('authored', Cause.UNKNOWN, edit.name, candidate)


def delta(before, after) -> dict:
    """Keep copy-level losses visible, including score losses in inexact code."""
    lost = sorted(before.exact_functions() - after.exact_functions())
    gained = sorted(after.exact_functions() - before.exact_functions())
    changes = [{'symbol': symbol, 'before': score, 'after': after.fuzzy.get(symbol, 0)}
               for symbol, score in before.fuzzy.items() if after.fuzzy.get(symbol, 0) != score]
    return {'lost_exact': lost, 'gained_exact': gained, 'changes': changes,
            'worse': [c for c in changes if c['after'] < c['before']]}


def counterfactual(harvest, header: str, line: int | None, save: Path, units: list[str] | None = None,
                   authored: tuple[str, str] | None = None, variant: str | None = None) -> dict:
    """Measure line rewrites or a registered correction without writing the header."""
    source = (harvest.root / header).read_bytes()
    started = time.monotonic()
    if variant is not None and authored is None:
        raise ValueError('--variant requires --authored')
    if authored is not None:
        from concord.authored import header_edits

        unit, symbol = authored
        unit = harvest.source(unit)
        edits = header_edits(unit, symbol, variant)
        if not edits:
            raise ValueError(f'no authored header spellings for {unit} {symbol}')
        candidates = [authored_rewrite(source, header, unit, symbol, edit) for edit in edits]
    elif line is not None:
        candidates = rewrites(source, line)
    else:
        raise ValueError('a source line or an authored correction is required')
    dependents = harvest.dependents(header)
    selected = units if units is not None else dependents
    if not set(selected) <= set(dependents):
        raise ValueError('every selected unit must include the header')
    directory = save.parent / (save.stem + '-candidates')
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for number, rewrite in enumerate(candidates):
        path = directory / f'{number}.h'
        path.write_bytes(rewrite.source)
        rows.append({'description': rewrite.description, 'transform': rewrite.transform, 'source': str(path),
                     'sha256': hashlib.sha256(rewrite.source).hexdigest(), 'units': []})
    result = {'schema': 'concord.causal-trial.v1', 'header': header, 'line': line,
              'header_sha256': hashlib.sha256(source).hexdigest(), 'dependents': dependents,
              'selected': selected, 'candidates': rows, 'jobs': max(1, int(os.environ.get('CONCORD_JOBS', '8'))),
              'config': {name: hashlib.sha256((harvest.root / 'config' / harvest.build / name).read_bytes()).hexdigest()
                         for name in ('flags.json', 'units.toml', 'symbols.tsv')}}
    if authored is not None:
        result['authored'] = {'unit': unit, 'symbol': symbol}
    if not candidates:
        save.write_text(json.dumps(result, indent=2) + '\n')
        return result
    for index, unit in enumerate(selected, 1):
        print(f'{index}/{len(selected)} {unit}', flush=True)
        unit_path = harvest.root / 'src' / unit
        unit_hash = hashlib.sha256(unit_path.read_bytes()).hexdigest()
        items = {'before': (unit, {})}
        items.update({str(n): (unit, {header: r.source}) for n, r in enumerate(candidates)})
        evaluations = harvest.evaluate_overlays(items)
        baseline = evaluations['before']
        if baseline.error:
            raise RuntimeError(baseline.error)
        for number, row in enumerate(rows):
            evaluation = evaluations[str(number)]
            measured = {'unit': unit, 'source_sha256': unit_hash, 'error': evaluation.error,
                        'baseline_object': str(baseline.object), 'object': str(evaluation.object)}
            if not evaluation.error:
                measured.update(delta(baseline, evaluation))
                measured['object_sha256'] = hashlib.sha256(evaluation.object.read_bytes()).hexdigest()
                measured['baseline_object_sha256'] = hashlib.sha256(baseline.object.read_bytes()).hexdigest()
            row['units'].append(measured)
        if hashlib.sha256(unit_path.read_bytes()).hexdigest() != unit_hash:
            raise RuntimeError(f'unit changed during counterfactual measurement: {unit}')
        result['elapsed_seconds'] = time.monotonic() - started
        save.write_text(json.dumps(result, indent=2) + '\n')
    if (harvest.root / header).read_bytes() != source:
        raise RuntimeError('header changed during counterfactual measurement')
    save.write_text(json.dumps(result, indent=2) + '\n')
    return result
