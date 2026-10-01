"""Source rewrites intended to preserve semantics that steer codegen.

Because the residual gap is codegen, the search's moves are source rewrites that
leave behavior unchanged but change the compiler's output: reorder commutative
operands, change local declaration order, hoist or sink temporaries, switch loop
form or condition direction, force or block inlining, reorder struct fields
within recovered constraints, pick signed versus unsigned. Whether a rewrite is
safe can depend on the function, so every application is checked against the
oracle, and one that changes behavior on its tested inputs is rejected.
"""

from __future__ import annotations

from collections.abc import Callable

from concord.model import Candidate

# A transform maps a candidate to zero or more new candidates (a move may have
# several concrete applications, for example each commutative operand site).
Transform = Callable[[Candidate], list[Candidate]]

_REGISTRY: dict[str, Transform] = {}


def register(name: str) -> Callable[[Transform], Transform]:
    def wrap(fn: Transform) -> Transform:
        _REGISTRY[name] = fn
        return fn

    return wrap


def registry() -> dict[str, Transform]:
    return dict(_REGISTRY)


# Transform implementations are added here as they are built, each tagged with the
# Cause it is meant to address so search.py can select by attributed cause.
