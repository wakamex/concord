"""Cheap, read-only checks of weak symbol names against compiled code."""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import capstone
from capstone import x86
from elftools.elf.elffile import ELFFile

from concord.symbol_table import load

RAX = {'rax', 'eax', 'ax', 'al', 'ah'}


def decoder():
    result = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    result.detail = True
    result.skipdata = True
    return result


def sections(path: Path) -> list[tuple[int, bytes]]:
    with path.open('rb') as stream:
        elf = ELFFile(stream)
        if elf['e_machine'] != 'EM_X86_64':
            raise ValueError(f'{path}: only x86-64 return conventions are checked')
        return [(s['sh_addr'], s.data()) for s in elf.iter_sections() if s['sh_flags'] & 4 and s['sh_size']]


def explicit_return(code: bytes, address: int) -> list[int]:
    """A saved value deliberately moved into RAX in a return epilogue.

    Ignore incidental RAX values left by calls. This narrow pattern is evidence
    of a returned value, not a general return-type inference algorithm.
    """
    instructions = list(decoder().disasm(code, address))
    found = []
    for index, instruction in enumerate(instructions):
        operands = instruction.operands
        if instruction.mnemonic != 'mov' or len(operands) != 2 or operands[0].type != x86.X86_OP_REG:
            continue
        if instruction.reg_name(operands[0].reg) != 'rax':
            continue
        saved_register = (operands[1].type == x86.X86_OP_REG
                          and instruction.reg_name(operands[1].reg) in ('rbx', 'rbp', 'r12', 'r13', 'r14', 'r15'))
        saved_stack = (operands[1].type == x86.X86_OP_MEM
                       and instruction.reg_name(operands[1].mem.base) == 'rsp' and not operands[1].mem.index)
        if not (saved_register or saved_stack):
            continue
        for following in instructions[index + 1:index + 9]:
            if following.mnemonic == 'ret':
                found.append(instruction.address)
                break
            reads, writes = following.regs_access()
            if any(following.reg_name(r) in RAX for r in (*reads, *writes)) or following.mnemonic not in ('mov', 'pop', 'add', 'leave', 'nop'):
                break
    return found


def return_users(executable: list[tuple[int, bytes]], addresses: set[int]) -> dict[int, list[int]]:
    """Direct call sites reading RAX before replacing it or leaving the block."""
    users = defaultdict(list)
    md = decoder()
    for start, data in executable:
        pending = None
        for instruction in md.disasm(data, start):
            if not instruction.id:
                pending = None
                continue
            if pending:
                target, call, count = pending
                reads, writes = instruction.regs_access()
                clears = (instruction.mnemonic in ('xor', 'sub') and len(instruction.operands) == 2
                          and all(op.type == x86.X86_OP_REG for op in instruction.operands)
                          and instruction.operands[0].reg == instruction.operands[1].reg)
                if not clears and any(instruction.reg_name(r) in RAX for r in reads):
                    users[target].append(call)
                    pending = None
                elif any(instruction.reg_name(r) in RAX for r in writes) or instruction.group(capstone.CS_GRP_JUMP) or instruction.mnemonic in ('call', 'ret') or count >= 8:
                    pending = None
                else:
                    pending = (target, call, count + 1)
            if instruction.mnemonic == 'call' and instruction.operands and instruction.operands[0].type == x86.X86_OP_IMM:
                target = instruction.operands[0].imm
                if target in addresses:
                    pending = (target, instruction.address, 0)
    return users


def void_contract(name: str) -> str | None:
    # This private libstdc++ helper returns void; the public erase(iterator)
    # returns the next iterator. Keep the rule restricted to that library type.
    if name.startswith('_ZNSt') and '4listI' in name and '8_M_eraseESt14_List_iterator' in name:
        return 'libstdc++ std::list::_M_erase(iterator) returns void'
    return None


def analyze(rows: list, target: Path, objects: list[Path]) -> dict:
    wanted = {row.symbol for row in rows}
    emitted = defaultdict(list)
    inputs = {}
    for path in objects:
        with path.open('rb') as stream:
            elf = ELFFile(stream)
            table = elf.get_section_by_name('.symtab')
            if table is None:
                continue
            for symbol in table.iter_symbols():
                if symbol.name in wanted and symbol['st_info']['type'] == 'STT_FUNC' and isinstance(symbol['st_shndx'], int):
                    emitted[symbol.name].append({'object': str(path), 'size': symbol['st_size']})
                    if str(path) not in inputs:
                        inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    executable = sections(target)
    users = return_users(executable, {row.address for row in rows})
    findings = []
    for row in rows:
        copies = emitted[row.symbol]
        code = next((data[row.address - start:row.address - start + row.size] for start, data in executable
                     if start <= row.address and row.address + row.size <= start + len(data)), None)
        reasons = []
        in_code = any(start <= row.address < start + len(data) for start, data in executable)
        if code is None and in_code:
            reasons.append('size crosses the executable section boundary')
        if copies and row.size not in {copy['size'] for copy in copies}:
            reasons.append('target size disagrees with every emitted copy; context or naming needs inspection')
        returns = explicit_return(code, row.address) if code is not None else []
        contract = void_contract(row.symbol)
        if contract and (returns or users[row.address]):
            reasons.append('target provides or callers consume an integer/pointer result, contradicting the named void helper')
        findings.append({'address': row.address, 'size': row.size, 'symbol': row.symbol, 'evidence': row.evidence,
                         'status': 'suspect' if reasons else 'not-executable' if not in_code else 'checked' if copies else 'unverified-not-emitted',
                         'reasons': reasons, 'emitted_copies': copies, 'explicit_return_sites': returns,
                         'return_value_call_sites': users[row.address], 'return_contract': contract})
    return {'schema': 'concord.symbol-audit.v1', 'target': str(target),
            'target_sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'objects': inputs, 'findings': findings}


def run(harvest: Path, kinds: list[str] | None = None) -> dict:
    manifest = json.loads((harvest / 'builds.json').read_text())
    build = manifest['canonical']
    images = manifest['builds'][build]['images']
    if len(images) != 1:
        raise ValueError('the canonical build must name one target image')
    name, pin = next(iter(images.items()))
    target = harvest / 'orig' / build / name
    if target.stat().st_size != pin['size'] or hashlib.sha256(target.read_bytes()).hexdigest() != pin['sha256']:
        raise ValueError('target image differs from its pinned size/hash')
    path = harvest / 'config' / build / 'symbols.tsv'
    rows = [row for row in load(path).values() if row.evidence.split(':', 1)[0] in (kinds or ['manual'])]
    report = analyze(rows, target, sorted((harvest / 'build' / 'match' / build).glob('*.o')))
    report['symbols_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    report['evidence_kinds'] = kinds or ['manual']
    return report
