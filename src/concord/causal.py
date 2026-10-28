"""Trace differing instructions through pinned GCC tree-SSA and RTL dumps.

The target supplies an assembly constraint, not compiler dumps. A trace proves
where our instruction was introduced or assigned registers; it does not prove
which source edit would make the target's compiler decision.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import uuid
from functools import cache
from itertools import pairwise
from pathlib import Path

from concord.diff import read_function
from concord.harvest import Harvest
from concord.lines import inline_chains
from concord.model import Cause

SCHEMA = "concord.causal.v1"
ANALYZER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PASS = re.compile(r"\.(\d+)r\.([\w_]+)$")
HEAD = re.compile(r"^;; Function (.+)$", re.MULTILINE)
INSN = re.compile(r"^\((?:insn|jump_insn|call_insn) (\d+) ", re.MULTILINE)
REGISTER = re.compile(r"\(reg(?:/[a-z]+)?:\w+\s+(\d+)(?:\s+([a-z][a-z0-9]*))?")
TREE_PASS = re.compile(r"\.(\d+)t\.([\w_]+)$")
SSA = re.compile(r"\b(?:[A-Za-z_$][\w.$]*|D\.\d+)_(\d+)(?:\([DAB]\))?")
TREE_LOCATION = re.compile(r"\[([^\[\]]+?)\s*:\s*(\d+)(?::\d+)?\]")
TREE_MIRROR = {'<': '>', '>': '<', '<=': '>=', '>=': '<=', '==': '==', '!=': '!='}


def function_text(text: str, symbol: str) -> str:
    headers = list(HEAD.finditer(text))
    matches = [i for i, h in enumerate(headers) if re.search(r"\(" + re.escape(symbol) + r"(?:[, )])", h[1])]
    if len(matches) != 1:
        return ''
    index = matches[0]
    return text[headers[index].end():headers[index + 1].start() if index + 1 < len(headers) else len(text)]


def ssa_variable(name: str) -> str:
    return re.sub(r'_\d+(?:\([DAB]\))?$', '', name)


def same_comparison(row: dict, variables: list[str], operator: str) -> bool:
    actual = [ssa_variable(v) for v in row['operands']]
    return ((actual == variables and row['operator'] == operator)
            or (actual == variables[::-1] and TREE_MIRROR[row['operator']] == operator))


def tree_function(text: str, symbol: str) -> dict:
    """Read statements, SSA definitions and comparisons from GCC 4.4 text.

    Declaration UIDs distinguish repeated inlined locals with the same spelling.
    Diagnostic prose and value-range summaries are excluded by requiring a
    basic-block header. An SSA definition is never a range-summary entry.
    """
    definitions, comparisons, statements = {}, [], []
    block = None
    chunk = function_text(text, symbol)
    bodies = list(re.finditer(r'^[^\s{};][^\n;]*\([^\n]*\)[^\n;]*\n(?:Eh tree:\n(?:[ \t]+[^\n]*\n)*)?\{\s*$', chunk, re.MULTILINE))
    if bodies:
        chunk = chunk[bodies[-1].end():]
    for number, raw in enumerate(chunk.splitlines(), 1):
        head = re.match(r'\s*(?:<bb (\d+)>\s*:|# BLOCK (\d+)\b)', raw)
        if head:
            block = int(head[1] or head[2])
            continue
        if block is None:
            continue
        location = TREE_LOCATION.search(raw)
        source = {'path': location[1].strip(), 'line': int(location[2])} if location else None
        line = TREE_LOCATION.sub('', raw).strip().removeprefix('# ').strip()
        row = {'text': line, 'block': block, 'dump_line': number, 'source': source}
        assignment = re.match(r'(' + SSA.pattern + r')\s*=\s*(.*)', line)
        if assignment:
            name, rhs = assignment[1], assignment[3]
            row.update(name=name, inputs=list(dict.fromkeys(m[0] for m in SSA.finditer(rhs))),
                       kind='phi' if rhs.startswith('PHI <') else 'assignment')
            definitions[name] = row
            statements.append(row)
        elif line.endswith(';'):
            statements.append(row)
        comparison = re.match(r'if\s*\((.+?)\s+(<=|>=|==|!=|<|>)\s+(.+)\)\s*$', line)
        if comparison:
            row.update(operands=[comparison[1], comparison[3]], operator=comparison[2])
            comparisons.append(row)
    return {'definitions': definitions, 'comparisons': comparisons, 'statements': statements}


def tree_history(directory: Path, symbol: str, operands: list[str], *, operator: str | None = None) -> dict:
    """Follow one uniquely identified pair of declarations across SSA passes.

    The comparison's source spelling can reverse and loop copying can replace
    an SSA operand. Declaration identity, rather than a block number or an
    occurrence count, links those passes. Ambiguous copies remain unresolved.
    """
    variables = [ssa_variable(name) for name in operands]
    identities = operands if variables[0] == variables[1] else variables
    paths = sorted((p for p in directory.iterdir() if TREE_PASS.search(p.name)),
                   key=lambda p: int(TREE_PASS.search(p.name)[1]))
    comparisons, changes, mirrors, snapshots = [], [], [], []
    previous_order = None
    for path in paths:
        number, name = TREE_PASS.search(path.name).groups()
        text = path.read_text(errors='replace')
        if not function_text(text, symbol):
            continue
        parsed = tree_function(text, symbol)
        stage = {'pass': name, 'pass_number': int(number), 'dump': str(path)}
        snapshots.append((stage, parsed))
        matches = []
        for row in parsed['comparisons']:
            actual = row['operands'] if variables[0] == variables[1] else [ssa_variable(v) for v in row['operands']]
            if sorted(actual) != sorted(identities) or not all(SSA.fullmatch(v) for v in row['operands']):
                continue
            if operator is not None and not ((actual == identities and row['operator'] == operator)
                                             or (actual == identities[::-1] and TREE_MIRROR[row['operator']] == operator)):
                continue
            matches.append(row)
        if len(matches) > 1:
            matches = [row for row in matches if sorted(row['operands']) == sorted(operands)]
        if len(matches) != 1:
            continue
        comparison = {**stage, **matches[0]}
        if comparisons and comparison['text'] == comparisons[-1]['text']:
            continue
        comparisons.append(comparison)
        if len(comparisons) > 1:
            before = comparisons[-2]
            before_names = before['operands'] if variables[0] == variables[1] else [ssa_variable(v) for v in before['operands']]
            after_names = comparison['operands'] if variables[0] == variables[1] else [ssa_variable(v) for v in comparison['operands']]
            if (before_names == after_names[::-1]
                    and TREE_MIRROR[before['operator']] == comparison['operator']):
                mirrors.append({'kind': 'comparison-mirrored', 'before': before, 'after': comparison})
        by_variable = {(v if variables[0] == variables[1] else ssa_variable(v)): int(SSA.fullmatch(v)[1]) for v in comparison['operands']}
        order = by_variable[identities[0]] < by_variable[identities[1]]
        if previous_order is not None and order != previous_order:
            changes.append({'kind': 'ssa-version-order-change', 'before': comparisons[-2], 'after': comparison})
        previous_order = order

    owners = {}
    for index, (stage, parsed) in enumerate(snapshots):
        for old, definition in parsed['definitions'].items():
            owners.setdefault(int(SSA.fullmatch(old)[1]), []).append((index, stage, old, definition))

    @cache
    def origin(value: str, through: int | None = None) -> dict:
        # Only the last uninterrupted lifetime is relevant. A removed version
        # can be recycled for an unrelated value in a later pass.
        creation, previous, last, created_at = None, False, None, 0
        for index, (stage, parsed) in enumerate(snapshots[:through]):
            definition = parsed['definitions'].get(value)
            present = definition is not None
            if present and not previous:
                creation = {**stage, 'definition': definition}
                created_at = index
            if present:
                last = {**stage, 'definition': definition}
            previous = present
        version = int(SSA.fullmatch(value)[1])
        recycled = None
        for index, stage, old, definition in owners.get(version, []):
            if index < created_at and old != value:
                recycled = {**stage, 'name': old, 'definition': definition, 'snapshot': index}
        return {'name': value, 'variable': ssa_variable(value), 'version': version,
                'origin_kind': 'function-entry' if value.endswith('(D)') else 'statement' if creation else 'unresolved',
                'creation': creation, 'last_definition': last, 'recycled_from': recycled}

    traced = [origin(value) for value in operands]
    # Record the actual def-use slice feeding the compared names, including
    # the loop exit PHI's input, rather than attributing every site to its test.
    dependencies, pending, visited = [], list(operands), set()
    while pending:
        value = pending.pop(0)
        if value in visited:
            continue
        visited.add(value)
        entry = origin(value)
        if entry['creation']:
            dependencies.append(entry)
            pending.extend(entry['creation']['definition']['inputs'])
            pending.extend(entry['last_definition']['definition']['inputs'])
    recycling, pending_recycled, seen_recycled = [], [], set()
    for entry in dependencies:
        if entry['recycled_from']:
            previous = entry['recycled_from']
            pending_recycled.append((previous['name'], previous['snapshot'] + 1))
    while pending_recycled:
        value, through = pending_recycled.pop(0)
        if (value, through) in seen_recycled:
            continue
        seen_recycled.add((value, through))
        entry = origin(value, through)
        if entry['creation']:
            recycling.append(entry)
            pending_recycled.extend((v, through) for v in entry['creation']['definition']['inputs'])
            pending_recycled.extend((v, through) for v in entry['last_definition']['definition']['inputs'])
    return {'status': 'observed-ssa-pair' if comparisons else 'unresolved', 'operands': traced,
            'comparisons': comparisons, 'order_changes': changes, 'dependencies': dependencies,
            'orientation_changes': mirrors,
            'recycling_dependencies': recycling,
            'target_history': 'unavailable; reversed creation order is a counterfactual constraint'}


def tree_scopes(text: str, symbol: str) -> dict[str, list[dict]]:
    """Declaration UIDs and inline call scopes printed by scope cleanup."""
    scopes, result = [], {}
    for line in function_text(text, symbol).splitlines():
        if '{ Scope block #' in line:
            location = re.search(r'([^\s{}]+):(\d+)', line)
            scopes.append({'path': location[1], 'line': int(location[2])} if location else None)
        elif scopes and line.strip() == '}':
            scopes.pop()
        elif scopes:
            variable = re.search(r'\b([\w.$]+D\.\d+)\s*;', line)
            if variable:
                result[variable[1]] = [s for s in scopes if s]
    return result


def expanded_statements(text: str, symbol: str) -> dict[int, dict]:
    """Join instruction IDs to the GIMPLE block and statement printed by expand.

    GCC renumbers RTL blocks after emitting them, so use the per-statement
    section before the full RTL, rather than the final RTL block numbers.
    """
    chunk = function_text(text, symbol).split(';; Full RTL generated for this function:')[0]
    block, statement, result = None, None, {}
    for line in chunk.splitlines():
        head = re.match(r';; Generating RTL for gimple basic block (\d+)', line)
        if head:
            block, statement = int(head[1]), None
        elif line.startswith(';; '):
            statement = line[3:].strip()
        else:
            instruction = INSN.match(line)
            if instruction and block is not None and statement:
                result[int(instruction[1])] = {'block': block, 'text': statement}
    return result


def replacement_map(text: str, symbol: str) -> tuple[dict[int, str], dict[int, str]]:
    """GCC's authoritative temporary replacements and SSA partition names."""
    chunk = function_text(text, symbol)
    replacements = {int(m[1]): m[2] for m in re.finditer(
        r'^\S+_(\d+)(?:\([DAB]\))? replace with --> .*? = (.*);$', chunk, re.MULTILINE)}
    base = chunk.partition('After Base variable replacement:')[2]
    partitions = {}
    for match in re.finditer(r'^Partition \d+ \((.+) - ([\d ]+)\)$', base, re.MULTILINE):
        for version in match[2].split():
            partitions[int(version)] = match[1]
    return replacements, partitions


def expression_key(text: str):
    """Compare expression trees, accepting redundant parentheses only.

    GIMPLE's MEM notation is not C++. Expressions outside the C++ grammar are
    rejected rather than compared by dropping punctuation or choosing a line.
    """
    from tree_sitter import Parser

    from concord.transforms import CPP

    text = re.sub(r'(?<=[\w$])D\.\d+', '', text)
    text = re.sub(r'\b[A-Za-z_$][\w$]*(?:\.[\w$]+)+\b',
                  lambda match: 'gimple_' + match[0].encode().hex(), text)
    tree = Parser(CPP).parse(('int f() { return ' + text + '; }').encode())
    if tree.root_node.has_error:
        return None

    def key(node):
        if node.type == 'parenthesized_expression':
            return key(node.named_children[0])
        if not node.children:
            return node.type, node.text.decode()
        return node.type, tuple(key(child) for child in node.children)

    return key(tree.root_node)


def replaced_expression(value: str, replacements: dict[int, str], partitions: dict[int, str], seen=frozenset()) -> str | None:
    failed = False

    def replace(match):
        nonlocal failed
        version = int(match[1])
        if version in replacements and version not in seen:
            expanded = replaced_expression(replacements[version], replacements, partitions, seen | {version})
            if expanded is not None:
                return '(' + expanded + ')'
        if version in partitions:
            return partitions[version]
        failed = True
        return match[0]

    result = SSA.sub(replace, value)
    return None if failed else result


def block_context(parsed: dict, block: int, replacements: dict[int, str], partitions: dict[int, str]) -> dict:
    """Emitted definitions and ordered successors around a GIMPLE condition."""
    definitions, successors, other = set(), [], 0
    for row in parsed['statements']:
        if row['block'] != block or row.get('kind') == 'phi':
            continue
        jump = re.fullmatch(r'goto <bb (\d+)>;', row['text'])
        if jump:
            successors.append(int(jump[1]))
            continue
        if row.get('name'):
            version = int(SSA.fullmatch(row['name'])[1])
            if version in replacements:
                continue
            name = partitions.get(version)
            rhs = replaced_expression(row['text'].partition('=')[2].strip().removesuffix(';'), replacements, partitions)
            if name and rhs == name:
                continue
        else:
            match = re.match(r'([\w.$]+)\s*=', row['text'])
            name = re.sub(r'(?<=[\w$])D\.\d+', '', match[1]) if match else None
        if name:
            definitions.add(name)
        else:
            other += 1
    return {'definitions': sorted(definitions), 'successors': successors, 'other_statements': other}


def tree_comparison(directory: Path, symbol: str, uid: int) -> dict:
    """Resolve an RTL UID to a unique pre-expansion SSA comparison."""
    expand = next((p for p in directory.iterdir() if p.name.endswith('r.expand')), None)
    if expand is None:
        return {'status': 'unresolved', 'reason': 'no expand dump'}
    statement = expanded_statements(expand.read_text(errors='replace'), symbol).get(uid)
    if not statement or not statement['text'].startswith('if ('):
        return {'status': 'unresolved', 'reason': 'recollect dumps with expand details to join the instruction'}
    final = next((p for p in directory.iterdir() if p.name.endswith('t.final_cleanup')), None)
    if final is None:
        return {'status': 'unresolved', 'reason': 'no final GIMPLE dump'}
    final_parsed = tree_function(final.read_text(errors='replace'), symbol)
    rows = final_parsed['comparisons']
    rows = [r for r in rows if r['block'] == statement['block']]
    if len(rows) != 1:
        return {'status': 'unresolved', 'reason': 'expand block does not identify a unique comparison'}
    variables = rows[0]['operands']
    # With -uid each inline instance has distinct declaration names. Refuse
    # old dumps whose repeated len/i spellings would silently select a copy.
    direct = all(re.fullmatch(r'[\w.$]*D\.\d+', v) for v in variables)
    named_uids = {}
    scalar_types = {'int', 'char', 'bool', 'short', 'long', 'float', 'double', 'void', 'wchar_t'}
    for match in re.finditer(r'\b([\w.$]*[\w$])D\.(\d+)', function_text(final.read_text(errors='replace'), symbol)):
        named_uids.setdefault(match[1], set()).add(match[2])
    folded_names_unique = all(len(named_uids[name]) == 1 for value in variables
                              if not re.fullmatch(r'[\w.$]*D\.\d+', value)
                              for name in re.findall(r'\b([\w.$]*[\w$])D\.\d+', value)
                              if name not in scalar_types)
    optimized = next((p for p in directory.iterdir() if p.name.endswith('t.optimized')), None)
    replacements, partitions = replacement_map(optimized.read_text(errors='replace'), symbol) if optimized else ({}, {})
    paths = sorted((p for p in directory.iterdir() if TREE_PASS.search(p.name)
                    and int(TREE_PASS.search(p.name)[1]) < 123),
                   key=lambda p: int(TREE_PASS.search(p.name)[1]), reverse=True)
    tried_replacements = False
    for path in paths:
        parsed = tree_function(path.read_text(errors='replace'), symbol)
        found = [r for r in parsed['comparisons'] if same_comparison(r, variables, rows[0]['operator'])] if direct else []
        method = 'declaration-uids'
        if len(found) != 1 and partitions and folded_names_unique and not tried_replacements:
            tried_replacements = True
            found = []
            wanted = [expression_key(v) for v in variables]
            if all(v is not None for v in wanted):
                for row in parsed['comparisons']:
                    if row['operator'] != rows[0]['operator'] or not all(SSA.fullmatch(v) for v in row['operands']):
                        continue
                    ambiguous = [(wanted, got) for wanted, got in zip(variables, row['operands'], strict=True)
                                 if any(len(named_uids[name]) > 1 for name in re.findall(r'\b([\w.$]*[\w$])D\.\d+', wanted)
                                        if name not in scalar_types)]
                    if any(wanted != ssa_variable(got) for wanted, got in ambiguous):
                        continue
                    expanded = [replaced_expression(v, replacements, partitions) for v in row['operands']]
                    if all(v is not None for v in expanded) and [expression_key(v) for v in expanded] == wanted:
                        found.append(row)
                method = 'temporary-expression-replacement'
        if found:
            alternatives = list(found)
            context = None
            if len(found) > 1 and partitions:
                context = block_context(final_parsed, rows[0]['block'], {}, {})
                if len(context['successors']) == 2:
                    found = [row for row in found if row['block'] == rows[0]['block']
                             and block_context(parsed, row['block'], replacements, partitions) == context]
                    method = 'gimple-block-context'
            if len(found) != 1 or not all(SSA.fullmatch(v) for v in found[0]['operands']):
                return {'status': 'unresolved', 'reason': 'multiple SSA comparisons use this declaration pair',
                        'ssa_candidates': [{**row, 'dump': str(path),
                                            'definitions': {v: parsed['definitions'].get(v) for v in row['operands']}}
                                           for row in alternatives]}
            history = tree_history(directory, symbol, found[0]['operands'], operator=found[0]['operator'])
            scopes = {}
            for dump in reversed(paths):
                scopes.update(tree_scopes(dump.read_text(errors='replace'), symbol))
            for operand in history['dependencies'] + history['recycling_dependencies']:
                operand['scopes'] = scopes.get(operand['variable'], [])
            return {**history,
                    'join': {'instruction_uid': uid, 'expanded': statement, 'gimple': rows[0], 'ssa_dump': str(path), 'method': method,
                             'block_context': context,
                             'replacements': replacements if method == 'temporary-expression-replacement' else {},
                             'partitions': partitions if method == 'temporary-expression-replacement' else {}}}
    return {'status': 'unresolved', 'reason': 'no SSA comparison with these declaration UIDs'}


def proposals(source: bytes, symbol: str, lines: set[int], protected: set[int]) -> list:
    """Existing rewrites restricted to traced origins and earlier constructs.

    Never rewrite the differing comparison. Earlier statements near an origin
    are counterfactuals, not asserted causes; only an overlay can establish
    that they control the observed order.
    """
    from concord import transforms

    scope = transforms.find_function(source, symbol)
    # Compute coordinates from bytes. Repeated Node Point reads corrupt the
    # binding's cached coordinates on Python 3.13 for source rows above 256.
    def bounds(node):
        return source.count(b'\n', 0, node.start_byte) + 1, source.count(b'\n', 0, node.end_byte) + 1

    statements = sorted(bounds(node) for kind in ('declaration', 'expression_statement', 'if_statement', 'for_statement', 'while_statement')
                        for node in transforms._descendants(scope, kind))
    first, last = bounds(scope)
    anchors = {line for line in lines if first <= line <= last}
    if not anchors:
        return []
    allowed = set(anchors)
    for anchor in anchors:
        earlier = [(first, last) for first, last in statements if last < anchor]
        for first, last in earlier[-3:]:
            allowed.update(range(first, last + 1))
    generators = (transforms.swap_statements, transforms.split_declaration, transforms.join_declaration,
                  transforms.move_declarations, transforms.name_temporaries, transforms.inline_temporaries,
                  transforms.for_loop, transforms.while_loop, transforms.swap_branches,
                  transforms.expand_assignment, transforms.compress_assignment, transforms.swap_commutative,
                  transforms.nest_condition, transforms.merge_conditions)
    found, seen = [], set()
    for generator in generators:
        for rewrite in generator(source, scope):
            changed = set()
            for tag, start, end, _, _ in difflib.SequenceMatcher(None, source.splitlines(), rewrite.source.splitlines(), autojunk=False).get_opcodes():
                if tag != 'equal':
                    changed.update(range(start + 1, max(start + 1, end) + 1))
            if not changed or changed & protected or not changed & allowed or rewrite.source in seen:
                continue
            seen.add(rewrite.source)
            found.append((min(abs(line - anchor) for line in changed for anchor in anchors), rewrite))
    return [rewrite for _, rewrite in sorted(found, key=lambda item: item[0])]


def search(harvest: Harvest, report: dict, *, budget: int = 64, apply: bool = False) -> dict:
    """Measure traced caller rewrites as overlays, then use author's gate."""
    from concord import authored

    if budget < 1:
        raise ValueError('the causal search budget must be positive')
    unit, symbol = harvest.source(report['unit']), report['symbol']
    source = (harvest.root / 'src' / unit).read_bytes()
    lines, protected = set(), set()
    for finding in report['findings']:
        if (finding.get('source') or {}).get('path') == 'src/' + unit:
            protected.add(finding['source']['line'])
        tree = finding.get('tree_ssa', {})
        for entry in tree.get('dependencies', []) + tree.get('recycling_dependencies', []):
            locations = list(entry.get('scopes', []))
            for key in ('creation', 'last_definition', 'recycled_from'):
                if entry.get(key) and entry[key]['definition'].get('source'):
                    locations.append(entry[key]['definition']['source'])
            for location in locations:
                if location['path'].removeprefix('/work/') == 'src/' + unit:
                    lines.add(location['line'])
        for frame in finding.get('inline_chain', []):
            if frame['path'] == 'src/' + unit:
                lines.add(frame['line'])
    try:
        rewrites = proposals(source, symbol, lines, protected)[:budget]
    except KeyError:
        return {'status': 'header-definition', 'reason': 'no function body in this unit; no shared-header rewrite proposed', 'candidates': []}
    if not rewrites:
        return {'status': 'no-proposals', 'origin_lines': sorted(lines), 'candidates': []}
    items = {'baseline': (unit, {})}
    items.update({f'r{n}': (unit, {'src/' + unit: rewrite.source}) for n, rewrite in enumerate(rewrites)})
    evaluations = harvest.evaluate_overlays(items)
    before = evaluations['baseline']
    if before.error:
        raise RuntimeError(before.error)
    rows = []
    for n, rewrite in enumerate(rewrites):
        after = evaluations[f'r{n}']
        verdict = after.verdict(symbol)
        worse = sorted(name for name, score in before.fuzzy.items() if after.fuzzy.get(name, -1) < score)
        rows.append({'name': f'r{n}', 'description': rewrite.description, 'transform': rewrite.transform,
                     'source_sha256': hashlib.sha256(rewrite.source).hexdigest(), 'error': after.error or None,
                     'object_sha256': hashlib.sha256(after.object.read_bytes()).hexdigest() if after.object else None,
                     'before': before.fuzzy.get(symbol), 'after': after.fuzzy.get(symbol),
                     'exact': bool(verdict and verdict.row['exact']), 'worse': worse,
                     'regressions': {name: {'before': before.fuzzy[name], 'after': after.fuzzy.get(name)} for name in worse},
                     'lost_exact': sorted(before.exact_functions() - after.exact_functions()),
                     'eligible': authored.keep(before, after, symbol)})
    eligible = [n for n, row in enumerate(rows) if row['eligible']]
    chosen = max(eligible, key=lambda n: (rows[n]['exact'], rows[n]['after']), default=None)
    result = {'status': 'measured', 'origin_lines': sorted(lines), 'candidates': rows, 'chosen': None, 'applied': False,
              'baseline_source_sha256': hashlib.sha256(source).hexdigest(),
              'baseline_object_sha256': hashlib.sha256(before.object.read_bytes()).hexdigest() if before.object else None}
    if chosen is not None:
        from concord.finish import finish

        candidate = finish(harvest, unit, symbol, rewrites[chosen].source, source)
        if candidate is None:
            result['status'] = 'finishing-rejected'
            return result
        # The author path repeats the score checks and runs the behavior oracle
        # for an inexact improvement before any source write.
        accepted = authored.apply(harvest, unit, symbol, write=apply, candidate=candidate)
        result.update(chosen=rows[chosen]['name'] if accepted['chosen'] else None, applied=accepted['applied'],
                      oracle=accepted['candidates']['causal-candidate']['oracle'])
        path = Path(report['directory']) / 'authored-candidate.cpp'
        path.write_bytes(candidate)
        result['finished_source_sha256'] = hashlib.sha256(candidate).hexdigest()
        result['candidate_path'] = str(path)
    return result


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
    if cause == Cause.STACK_LAYOUT:
        for index, stage in enumerate(history):
            pattern = stage['pattern']
            if 'virtual-stack-vars' in pattern:
                return {"kind": "expand-stack-storage", "before": history[index - 1] if index else None, "after": stage}
            if re.search(r'\(mem[^ ]*[^\n]*\(reg(?:/[^: ]+)?:DI 7 sp\)', pattern):
                return {"kind": "post-expand-stack-storage", "before": history[index - 1] if index else None, "after": stage}
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
    return [_report(harvest, unit, symbol, directory, contexts[symbol][0], contexts[symbol][1], metadata,
                    mappings[symbol], selections[symbol], history[symbol]) for symbol in symbols]


def _report(harvest, unit, symbol, directory, canonical, traced, metadata, mapping, selected, history):
    findings = []
    locations = [traced.start + f.candidate for f in selected if f.candidate is not None]
    chains = iter(inline_chains(directory / 'trace.o', traced.section, locations))
    for f in selected:
        row = {"cause": f.cause.value, "target_offset": f.offset, "candidate_offset": f.candidate,
               "target_constraint": f.detail, "status": "unresolved", "decision": None, "source": None}
        if f.candidate is not None:
            row['inline_chain'] = [{'function': frame.function, 'path': frame.path, 'line': frame.line} for frame in next(chains)]
        if f.candidate in mapping:
            instruction = mapping[f.candidate]
            steps = history[instruction['uid']]
            observed = decision(steps, f.cause)
            row.update({"instruction": instruction, "history": steps, "decision": observed})
            if observed:
                row['status'] = 'observed-candidate-decision'
                row['source'] = source_construct(harvest.root, next((s['source'] for s in reversed(steps) if s['source']), None))
            if f.cause == Cause.OPERAND_ORDER:
                row['tree_ssa'] = tree_comparison(directory, symbol, instruction['uid'])
                tree = row['tree_ssa']
                for operand in tree.get('operands', []):
                    if operand['origin_kind'] == 'function-entry':
                        from concord.transforms import find_function

                        source = (harvest.root / 'src' / harvest.source(unit)).read_bytes()
                        try:
                            scope = find_function(source, symbol)
                        except KeyError:
                            continue
                        operand['declaration_source'] = source_construct(harvest.root, {
                            'path': 'src/' + harvest.source(unit), 'line': source.count(b'\n', 0, scope.start_byte) + 1})
                for operand in tree.get('dependencies', []) + tree.get('recycling_dependencies', []):
                    operand['scopes'] = [source_construct(harvest.root, location) for location in operand.get('scopes', [])]
                    for stage in ('creation', 'last_definition'):
                        if operand[stage]:
                            definition = operand[stage]['definition']
                            definition['source'] = source_construct(harvest.root, definition['source'])
        findings.append(row)
    return {"schema": SCHEMA, "analyzer_sha256": ANALYZER_SHA256, "unit": unit, "symbol": symbol, "directory": str(directory),
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
        tree = row.get('tree_ssa', {})
        if tree.get('status') == 'observed-ssa-pair':
            for operand in tree['operands']:
                creation = operand['creation']
                if creation:
                    lines.append(f"    {operand['name']} created in {creation['pass']}: {creation['definition']['text']}")
                elif operand['origin_kind'] == 'function-entry':
                    declaration = operand.get('declaration_source') or {}
                    lines.append(f"    {operand['name']} is a function-entry value: "
                                 f"{declaration.get('path', '?')}:{declaration.get('line', '?')}")
                if operand.get('scopes'):
                    caller = operand['scopes'][0]
                    lines.append(f"      {caller['path']}:{caller['line']}: {caller.get('text', '')}")
                if operand.get('recycled_from'):
                    previous = operand['recycled_from']
                    lines.append(f"      version recycled from {previous['name']} after {previous['pass']}: {previous['definition']['text']}")
            for change in tree['order_changes']:
                lines.append(f"    SSA version order reverses in {change['after']['pass']}: "
                             f"{change['before']['text']} -> {change['after']['text']}")
            for change in tree['orientation_changes']:
                lines.append(f"    comparison mirrored in {change['after']['pass']}: {change['after']['text']}")
        elif tree:
            lines.append(f"    tree SSA unresolved: {tree.get('reason', 'no unique pair')}")
    if search := report.get('search'):
        rows = search['candidates']
        lines.append(f"  caller search: {len(rows)} overlays, {sum(row.get('eligible', False) for row in rows)} eligible; "
                     f"selected {search.get('chosen') or 'none'}" + (' (applied through author)' if search.get('applied') else ''))
    return lines
