"""Known codegen idiosyncrasies of a compiler, indexed by the cause the diff
reports, each with the source change that fixed it and the evidence for it.

One TOML file per compiler version lives beside this module. The diff shows the
idioms for the causes it finds; the transform search reads the same entries to
choose its moves.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib import resources

from concord.model import Cause


@dataclass(frozen=True)
class Idiom:
    id: str
    cause: Cause
    status: str  # "confirmed" or "hypothesis"
    symptom: str
    fix: str
    before: str = ""
    after: str = ""
    limits: str = ""  # where the idiom does not apply, as measured
    evidence: tuple[dict, ...] = ()


def compilers() -> list[str]:
    return sorted(p.name.removesuffix(".toml") for p in resources.files(__package__).iterdir() if p.name.endswith(".toml"))


def load(compiler: str) -> list[Idiom]:
    data = tomllib.loads(resources.files(__package__).joinpath(f"{compiler}.toml").read_text())
    return [
        Idiom(
            id=row["id"],
            cause=Cause(row["cause"]),
            status=row["status"],
            symptom=row["symptom"],
            fix=row["fix"],
            before=row.get("before", ""),
            after=row.get("after", ""),
            limits=row.get("limits", ""),
            evidence=tuple(row.get("evidence", ())),
        )
        for row in data["idiom"]
    ]


def for_causes(idioms: list[Idiom], causes: set[Cause]) -> list[Idiom]:
    return [i for i in idioms if i.cause in causes]
