"""Find functions whose code depends on how many declarations precede them.

GCC 4.4 numbers every declaration it parses (DECL_UID) and keeps a function's
variables in a hash table keyed by that number, 31 slots wide for most
functions. Passes that walk the table visit the variables in slot order, so the
order two values are created in, and with it which operand of a comparison comes
first, can depend on the declaration count modulo the table's size. One more
declaration anywhere earlier in the translation unit, even an unused inline
member in a header, shifts every later number by one and can change such a
function's code; 31 more restore it.

`scan` compiles a unit with 0, 1, 2, ... extra declarations ahead of everything
else and records at which of those phases each function is exact. That sorts
the unit's functions into:

- locked: not exact as the source stands, exact at another phase. The body is
  right and the unit's headers declare the wrong number of things before it, so
  searching the body is wasted effort.
- witnesses: exact as the source stands and not at every phase. Each one is
  evidence that the count before it is right modulo the table size, and each one
  stops matching when a header it includes gains or loses a declaration.

The padding is a measurement, never a fix: the declarations a locked function
lacks are real ones the original headers had.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concord.harvest import Harvest

PHASES = 31  # slots in the hash table GCC 4.4 gives a function's variables before it grows


def pad(source: bytes, count: int) -> bytes:
    """The source with `count` extra declarations on its first line, which keeps
    every line number."""
    return b"".join(b"extern int concordPhase%d; " % n for n in range(count)) + source


@dataclass
class Scan:
    unit: str
    phases: int
    exact_at: dict[str, set[int]] = field(default_factory=dict)  # symbol -> phases at which it is exact
    errors: dict[int, str] = field(default_factory=dict)

    @property
    def locked(self) -> dict[str, set[int]]:
        return {s: p for s, p in self.exact_at.items() if p and 0 not in p}

    @property
    def witnesses(self) -> dict[str, set[int]]:
        return {s: p for s, p in self.exact_at.items() if 0 in p and len(p) < self.phases - len(self.errors)}

    def exact(self, phase: int) -> int:
        return sum(1 for p in self.exact_at.values() if phase in p)

    @property
    def best(self) -> int:
        """The phase with the most exact functions, the unit's own on a tie."""
        return max(range(self.phases), key=lambda k: (self.exact(k), k == 0, -k))


def scan(harvest: Harvest, unit: str, phases: int = PHASES) -> Scan:
    unit = harvest.source(unit)
    source = (harvest.root / "src" / unit).read_bytes()
    evaluations = harvest.evaluate(unit, {f"phase{k}": pad(source, k) for k in range(phases)})
    result = Scan(unit, phases)
    for k in range(phases):
        evaluation = evaluations[f"phase{k}"]
        if evaluation.error:
            result.errors[k] = evaluation.error
            continue
        for section in evaluation.sections:
            for row in section.get("functions", []):
                result.exact_at.setdefault(row["symbol"], set())
                if row["exact"]:
                    result.exact_at[row["symbol"]].add(k)
    return result


def spans(phases: set[int]) -> str:
    """Phases as ranges: {1, 2, 3, 7} is "1-3,7"."""
    out, run = [], []
    for k in [*sorted(phases), None]:
        if run and k == run[-1] + 1:
            run.append(k)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [k]
    return ",".join(out)
