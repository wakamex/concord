"""Turn a search's result into the source a person would submit.

Searches write rewrites in forms that are safe without type information: a
temporary as `__typeof__(E) concordTmpN = E;`, a negated comparison as
`!(a < b)`. `finish` rewrites them into ordinary C++, a temporary with its
concrete type and a name, a comparison inverted to `a >= b`, and keeps each
rewrite only if the function compiles to the same bytes. For a comparison that is
also the proof that the two spellings mean the same: GCC folds `!(a < b)` into
`a >= b` only when no operand can be NaN.
"""

from __future__ import annotations

from concord.diff import read_function
from concord.harvest import Evaluation, Harvest
from concord.temporaries import rename, variable_types
from concord.transforms import INVERSE, _descendants, find_function


def finish(harvest: Harvest, unit: str, symbol: str, source: bytes, original: bytes) -> bytes | None:
    """The source with its temporaries typed and named and its negated comparisons
    inverted where that leaves the function's bytes unchanged; None when the
    temporaries cannot be written without changing them."""
    reference = harvest.evaluate(unit, {"search": source})["search"]
    if reference.error:
        return None
    if b"concordTmp" in source:
        source = _temporaries(harvest, unit, symbol, source, reference)
        if source is None:
            return None
    return _comparisons(harvest, unit, symbol, source, original, reference)


def _same(evaluation: Evaluation, reference: Evaluation, symbol: str) -> bool:
    if evaluation.error or evaluation.exact_functions() != reference.exact_functions():
        return False
    return read_function(evaluation.object, symbol).code == read_function(reference.object, symbol).code


def _temporaries(harvest: Harvest, unit: str, symbol: str, source: bytes, reference: Evaluation) -> bytes | None:
    obj = harvest.debug_objects([unit], {unit: source}).get(unit)
    if obj is None:
        return None
    types = variable_types(obj)
    scope = find_function(source, symbol).text
    candidates = {}
    for direct in (True, False):  # direct initialization reads better; try it first
        finished = rename(source, types, direct, scope)
        if finished is not None and finished not in candidates.values():
            candidates[f"direct{int(direct)}"] = finished
    evaluations = harvest.evaluate(unit, candidates)
    return next((text for name, text in candidates.items() if _same(evaluations[name], reference, symbol)), None)


def negated_comparisons(source: bytes, original: bytes, symbol: str) -> list:
    """`!(a < b)` expressions in the function that the original function did not have."""
    before = {n.text for n in _negations(find_function(original, symbol))}
    return [n for n in _negations(find_function(source, symbol)) if n.text not in before]


def _negations(scope) -> list:
    found = []
    for node in _descendants(scope, "unary_expression"):
        argument = node.child_by_field_name("argument")
        if node.child_by_field_name("operator").type != "!" or argument.type != "parenthesized_expression":
            continue
        inner = argument.named_children[0]
        if inner.type == "binary_expression" and inner.child_by_field_name("operator").text in INVERSE:
            found.append(node)
    return found


def invert(source: bytes, node) -> bytes:
    inner = node.child_by_field_name("argument").named_children[0]
    operator = inner.child_by_field_name("operator")
    text = source[inner.start_byte : operator.start_byte] + INVERSE[operator.text] + source[operator.end_byte : inner.end_byte]
    return source[: node.start_byte] + text + source[node.end_byte :]


def _comparisons(harvest: Harvest, unit: str, symbol: str, source: bytes, original: bytes, reference: Evaluation) -> bytes:
    """Invert each new negated comparison on its own, keep the inversions that left
    the bytes unchanged, and check them together; if together they do not, the
    comparisons stay negated."""
    nodes = negated_comparisons(source, original, symbol)
    if not nodes:
        return source
    evaluations = harvest.evaluate(unit, {f"c{n}": invert(source, node) for n, node in enumerate(nodes)})
    keep = [node for n, node in enumerate(nodes) if _same(evaluations[f"c{n}"], reference, symbol)]
    inverted = source
    for node in sorted(keep, key=lambda n: n.start_byte, reverse=True):  # later offsets first
        inverted = invert(inverted, node)
    if len(keep) > 1 and not _same(harvest.evaluate(unit, {"all": inverted})["all"], reference, symbol):
        return source
    return inverted
