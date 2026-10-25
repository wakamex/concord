"""Measure default and explicit zero arguments of scalar vector resize calls."""

import re

from concord.blockers import _definitions
from concord.transforms import _descendants


def zero_resize(source: bytes, method: str, member: str, element_type: str) -> dict[str, bytes]:
    """Diagnostic forms for std::vector of a scalar or object pointer.

    Only the default value argument changes; existing element assignments stay
    in place. Class-valued elements need their constructor semantics checked
    separately and are deliberately unsupported here.
    """
    scalar = element_type in ('bool', 'char', 'int', 'unsigned int', 'short', 'unsigned short', 'long', 'unsigned long', 'float', 'double')
    pointer = re.fullmatch(r'(?:const\s+)?[\w:]+\s*\*', element_type)
    if not (scalar or pointer):
        raise ValueError('resize forms require a scalar or object-pointer element type')
    function = _definitions(source).get(method)
    if function is None:
        raise ValueError(f'method not found: {method}')
    calls = [node for node in _descendants(function, 'call_expression')
             if node.child_by_field_name('function').text.replace(b' ', b'') == member.encode() + b'.resize']
    if len(calls) != 1:
        raise ValueError('expected one resize call on the named vector member')
    arguments = calls[0].child_by_field_name('arguments')
    if len(arguments.named_children) != 1:
        raise ValueError('expected the original one-argument resize call')
    size = arguments.named_children[0].text
    return {'default': source, **{name: source[:arguments.start_byte] + b'(' + size + b', ' + value + b')' + source[arguments.end_byte:]
                                for name, value in [('zero', b'0'), ('typed-zero', b'(' + element_type.encode() + b')0')]}}


def diagnose(harvest, unit: str, method: str, member: str, element_type: str, restore: bool = False) -> dict:
    from pathlib import Path

    from concord.blockers import restored
    from concord.irrlicht import helper_names, name_helpers, translate
    from concord.seed import IRRLICHT, helper_taken, qualify

    source = (harvest.root / 'src' / unit).read_bytes()
    if restore:
        original = harvest.root / IRRLICHT / 'source' / 'Irrlicht' / Path(unit).name
        raw = translate(original.read_text(encoding='latin-1'))
        taken = helper_taken(harvest.root, raw, unit)
        translation = qualify(harvest.root, name_helpers(raw, taken), unit).encode()
        source = restored(source, translation, set(helper_names(raw, taken).values()), stubs_only=False)[method]
    forms = zero_resize(source, method, member, element_type)
    evaluations = harvest.evaluate(unit, forms)
    return {'unit': unit, 'method': method, 'member': member, 'element_type': element_type,
            'variants': {name: {'object': str(row.object), 'error': row.error, 'fuzzy': row.fuzzy,
                                'exact': len(row.exact_functions()), 'sections': row.sections}
                         for name, row in evaluations.items()}}
