"""Destructor definition forms for testing a unit's header context.

An implicit destructor can be materialized with an empty body: C++ still
inserts destruction of its fields and bases. Declaration-only candidates are
diagnostic unless another unit supplies the definition. These are compiler
experiments, not behavior-equivalence proofs; callers must check the build.
"""

from __future__ import annotations

from tree_sitter import Parser

from concord.transforms import CPP, _descendants


def forms(source: bytes, cls: str) -> dict[str, bytes]:
    """In-class, out-of-class inline, and declaration-only destructor candidates.

    Operate on a non-template class in its existing namespace. Preserve the
    destructor body, other members and access; an absent destructor is implicit
    and public. Reject an existing declaration without its body rather than
    inventing that body's behavior.
    """
    tree = Parser(CPP).parse(source)
    classes = [n for kind in ("class_specifier", "struct_specifier") for n in _descendants(tree.root_node, kind)
               if n.child_by_field_name("name") is not None and n.child_by_field_name("name").text == cls.encode()]
    if len(classes) != 1:
        raise ValueError(f"expected one definition of {cls}")
    node = classes[0]
    if node.parent.type == "template_declaration":
        raise ValueError("template classes need their template parameters on the definition")
    body = node.child_by_field_name("body")
    if body is None:
        raise ValueError(f"{cls} has no class body")
    destructor = next((n for n in body.named_children if any(d.text == b"~" + cls.encode()
                      for d in _descendants(n, "destructor_name"))), None)
    if destructor is not None and destructor.type != "function_definition":
        # A previous outline can be reversed using its real body, without
        # guessing the behavior of a declaration defined in another file.
        definitions = [n for n in _descendants(tree.root_node, "function_definition")
                       if n.child_by_field_name("declarator") is not None
                       and n.child_by_field_name("declarator").text == cls.encode() + b"::~" + cls.encode() + b"()"]
        if len(definitions) != 1 or definitions[0].start_byte < node.end_byte:
            raise ValueError("the existing destructor has no body in this header")
        definition = definitions[0]
        declaration = destructor.text.rstrip(b";").strip()
        in_class = declaration + b" " + definition.child_by_field_name("body").text
        restored = source[:definition.start_byte] + source[definition.end_byte:]
        restored = restored[:destructor.start_byte] + in_class + restored[destructor.end_byte:]
        return forms(restored, cls)
    if destructor is not None and any(n.type == "preproc_if" for n in body.named_children):
        raise ValueError("conditional member definitions need their preprocessor context")
    name = b"~" + cls.encode() + b"()"
    if destructor is None:
        # Put the public implicit member last, leaving every existing access
        # section and member declaration in its original position.
        start = end = body.end_byte - 1
        prefix, suffix = b"\npublic:\n    ", b"\n"
        code = b"{}"
    else:
        start, end = destructor.start_byte, destructor.end_byte
        code = destructor.child_by_field_name("body").text
        declaration = source[start:destructor.child_by_field_name("body").start_byte].strip()
        if declaration not in (name, b"virtual " + name):
            raise ValueError("destructor qualifiers or exception specifications need preservation")
        prefix, suffix = b"", b""
    candidates = {}
    for kind, replacement in (("explicit", name + b" " + code), ("outlined", name + b";"), ("declared", name + b";")):
        # Keep explicit virtual on an existing declaration. An implicit member
        # inherits virtualness from its base without adding a virtual slot.
        if destructor is not None and declaration.startswith(b"virtual "):
            replacement = b"virtual " + replacement
        text = source[:start] + prefix + replacement + suffix + source[end:]
        if kind == "outlined":
            # The terminating semicolon belongs to the enclosing declaration,
            # outside the class_specifier node.
            insertion = node.end_byte + len(text) - len(source)
            if text[insertion:insertion + 1] != b";":
                raise ValueError("class declarations with trailing declarators need separate handling")
            definition = b"\n\ninline " + cls.encode() + b"::" + name + b" " + code + b"\n"
            text = text[:insertion + 1] + definition + text[insertion + 1:]
        candidates[kind] = text
    return candidates


def detached(source: bytes, cls: str) -> tuple[bytes, bytes]:
    """A declaration-only header and its fully qualified inline definition.

    This allows testing definition timing in a unit, or splitting an inline
    implementation header. Every affected unit still needs the definition.
    """
    candidates = forms(source, cls)
    tree = Parser(CPP).parse(candidates["outlined"])
    definition = next(n for n in _descendants(tree.root_node, "function_definition")
                      if n.child_by_field_name("declarator") is not None
                      and n.child_by_field_name("declarator").text == cls.encode() + b"::~" + cls.encode() + b"()")
    namespaces = []
    parent = definition.parent
    while parent is not None:
        if parent.type == "namespace_definition":
            name = parent.child_by_field_name("name")
            if name is None:
                raise ValueError("anonymous namespace definitions cannot be detached globally")
            namespaces.append(name.text)
        parent = parent.parent
    declarator = definition.child_by_field_name("declarator")
    text = definition.text
    offset = declarator.start_byte - definition.start_byte
    return candidates["declared"], text[:offset] + b"::".join(reversed(namespaces)) + (b"::" if namespaces else b"") + text[offset:]


def diagnose(harvest, unit: str, header: str, cls: str) -> dict:
    """Compile definition-form overlays without changing the checkout."""
    from concord.inlines import read_code

    source = (harvest.root / "src" / header).read_bytes()
    candidates = {"baseline": (unit, {})}
    candidates.update({kind: (unit, {"src/" + header: text}) for kind, text in forms(source, cls).items()})
    declared, definition = detached(source, cls)
    text = (harvest.root / "src" / unit).read_bytes()
    candidates["deferred"] = (unit, {"src/" + header: declared, "src/" + unit: text + b"\n" + definition + b"\n"})
    evaluations = harvest.evaluate_overlays(candidates)
    baseline = evaluations["baseline"]
    rows = {}
    for name, evaluation in evaluations.items():
        rows[name] = {
            "error": evaluation.error,
            "object": str(evaluation.object),
            "exact": len(evaluation.exact_functions()),
            "lost_exact": sorted(baseline.exact_functions() - evaluation.exact_functions()),
            "worse": {s: [f, evaluation.fuzzy.get(s, 0)] for s, f in baseline.fuzzy.items() if evaluation.fuzzy.get(s, 0) < f},
            "destructor_sizes": {} if evaluation.error else {s: f.size for s, f in read_code(evaluation.object).functions.items() if s.endswith(("D0Ev", "D1Ev", "D2Ev"))},
        }
    return {"unit": unit, "header": header, "class": cls, "variants": rows}
