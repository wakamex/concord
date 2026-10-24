"""Distinguish a header definition from the context of its inlined copies."""

import json
from collections import defaultdict
from pathlib import Path

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from tree_sitter import Parser

from concord.transforms import CPP


def copy_index(directory: Path) -> dict:
    """Use the same emitted and exact-copy evidence as concord inlines."""
    result = defaultdict(lambda: {'emitted': set(), 'exact': set()})
    for path in directory.glob('*.json'):
        report = json.loads(path.read_text())
        for section in report.get('sections', []):
            for function in section.get('functions', []):
                row = result[function['symbol']]
                row['emitted'].add(report['unit'])
                if function.get('exact'):
                    row['exact'].add(report['unit'])
        for section in report.get('unplaced_sections', []):
            if section.startswith('.text.'):
                result[section[6:]]['emitted'].add(report['unit'])
    return {symbol: {key: sorted(units) for key, units in row.items()} for symbol, row in result.items()}


def definitions(source: bytes) -> list[tuple[int, int]]:
    """Definition ranges, including a multiline function's declaration."""
    parser = Parser(CPP)
    tree = parser.parse(source)
    found = []
    def visit(node):
        if node.type == 'function_definition':
            found.append((source.count(b'\n', 0, node.start_byte) + 1, source.count(b'\n', 0, node.end_byte) + 1))
        for child in node.named_children:
            visit(child)
    visit(tree.root_node)
    return found


def header_symbols(root: Path, obj: Path, paths: set[str]) -> dict:
    """Map header definition ranges to linkage names recorded by the compiler.

    Several template specializations can share a source range. Keep all of
    them visible rather than guessing a specialization from a source line.
    """
    from concord.census import source_path

    ranges = {path: definitions((root / path).read_bytes()) for path in paths if (root / path).is_file()}
    owners = defaultdict(set)
    with obj.open('rb') as stream:
        elf = ELFFile(stream)
        dwarf = elf.get_dwarf_info()
        table = elf.get_section_by_name('.symtab')
        functions = defaultdict(set)
        for symbol in table.iter_symbols():
            if symbol['st_info']['type'] == 'STT_FUNC':
                functions[(symbol['st_shndx'], symbol['st_value'])].add(symbol.name)
        addresses = {}
        for section in elf.iter_sections():
            if isinstance(section, RelocationSection) and elf.get_section(section['sh_info']).name == '.debug_info':
                for relocation in section.iter_relocations():
                    symbol = table.get_symbol(relocation['r_info_sym'])
                    addresses[relocation['r_offset']] = functions.get((symbol['st_shndx'], symbol['st_value'] + relocation['r_addend']), set())
        for cu in dwarf.iter_CUs():
            program = dwarf.line_program_for_CU(cu)
            if program is None:
                continue
            for die in cu.iter_DIEs():
                if die.tag != 'DW_TAG_subprogram':
                    continue
                chain, current = [], die
                while current is not None and current.offset not in {d.offset for d in chain}:
                    chain.append(current)
                    link = next((name for name in ('DW_AT_specification', 'DW_AT_abstract_origin') if name in current.attributes), None)
                    current = current.get_DIE_from_attribute(link) if link else None
                attrs = {}
                for item in reversed(chain):
                    attrs.update(item.attributes)
                name = attrs.get('DW_AT_linkage_name', attrs.get('DW_AT_MIPS_linkage_name'))
                file, line = attrs.get('DW_AT_decl_file'), attrs.get('DW_AT_decl_line')
                names = {name.value.decode()} if name else set()
                if 'DW_AT_low_pc' in attrs:
                    names.update(addresses.get(attrs['DW_AT_low_pc'].offset, set()))
                if not names or not file or not line:
                    continue
                modern = program.header.version >= 5
                entry = program['file_entry'][file.value if modern else file.value - 1]
                directory = (program['include_directory'][entry.dir_index if modern else entry.dir_index - 1]
                             if modern or entry.dir_index else b'')
                path = source_path(root, str(Path(directory.decode(errors='replace')) / entry.name.decode(errors='replace')))
                for start, end in ranges.get(path, []):
                    if start <= line.value <= end:
                        owners[(path, start, end)].update(names)
    return owners


def annotate(root: Path, reports: list[dict], symbols: dict, copies: dict) -> None:
    """Exact copies establish a working definition; absence is inconclusive."""
    for report in reports:
        for finding in report.get('findings', []):
            source = finding.get('source')
            if not source:
                continue
            from concord.census import source_path
            path = source_path(root, source['path'])
            if Path(path).suffix not in ('.h', '.hpp', '.tcc'):
                continue
            owners = sorted({symbol for (file, start, end), names in symbols.items()
                             if file == path and start <= source['line'] <= end for symbol in names})
            evidence = [{'symbol': name, **copies[name]} for name in owners if name in copies]
            exact = any(row['exact'] for row in evidence)
            finding['context'] = {
                'classification': 'caller-context' if exact else 'header-candidate' if evidence else 'header-unverified',
                'copies': evidence, 'definition_symbols': owners,
                'evidence': 'An exact out-of-line copy proves this source definition can match; caller or unit context remains.' if exact
                else 'No exact out-of-line copy is recorded; this does not establish a header defect.',
            }
