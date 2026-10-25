"""Strict three-way merges of Harvest symbol tables, keyed by symbol name."""

import csv
import io
from dataclasses import dataclass
from pathlib import Path

FIELDS = ['address', 'size', 'symbol', 'evidence']


@dataclass(frozen=True)
class Row:
    address: int
    size: int
    symbol: str
    evidence: str


def load(path: Path) -> dict[str, Row]:
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        if reader.fieldnames != FIELDS:
            raise ValueError(f'{path}: expected columns {FIELDS}')
        result = {}
        for number, row in enumerate(reader, 2):
            if None in row or any(row.get(key) is None for key in FIELDS):
                raise ValueError(f'{path}:{number}: malformed row')
            value = Row(int(row['address'], 16), int(row['size']), row['symbol'], row['evidence'])
            if not value.symbol or value.address < 0 or value.size < 0:
                raise ValueError(f'{path}:{number}: invalid symbol, address or size')
            if value.symbol in result:
                raise ValueError(f'{path}:{number}: duplicate symbol {value.symbol}')
            result[value.symbol] = value
        return result


def merge(base: dict, ours: dict, theirs: dict) -> list[Row]:
    result, conflicts = [], []
    for name in sorted(base.keys() | ours.keys() | theirs.keys()):
        a, b, c = base.get(name), ours.get(name), theirs.get(name)
        if b == c or c == a:
            chosen = b
        elif b == a:
            chosen = c
        else:
            conflicts.append(f'{name}: both sides changed the base row (including deletion)')
            continue
        if chosen is not None:
            result.append(chosen)
    addresses = {}
    for row in result:
        if row.address in addresses:
            conflicts.append(f'{row.address:#x}: claimed by {addresses[row.address]} and {row.symbol}')
        addresses[row.address] = row.symbol
    if conflicts:
        raise ValueError('\n'.join(conflicts))
    return sorted(result, key=lambda row: (row.address, row.symbol))


def render(rows: list[Row]) -> str:
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
    writer.writerow(FIELDS)
    for row in rows:
        writer.writerow([f'{row.address:#x}', row.size, row.symbol, row.evidence])
    return stream.getvalue()
