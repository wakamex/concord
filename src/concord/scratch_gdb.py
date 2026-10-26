"""Read scratch-search events from the pinned compiler under GNU Debugger.

Executed by the debugger, not imported by concord. All inferior accesses are
reads; breakpoints observe entry and the common return path.
"""

import json
import os
from pathlib import Path

import gdb

config = json.loads(Path(os.environ['CONCORD_SCRATCH_CONFIG']).read_text())
profile = config['profile']
inferior = gdb.selected_inferior()
events, errors = [], []
pending = None


def integer(address, size=8):
    return int.from_bytes(inferior.read_memory(address, size).tobytes(), 'little')


def text(address):
    return gdb.Value(address).cast(gdb.lookup_type('char').pointer()).string(errors='replace')


def reg(name):
    return int(gdb.parse_and_eval('$' + name))


def function_name():
    function = integer(profile['cfun'])
    declaration = integer(function + 0x30)
    identifier = integer(declaration + 0x80)
    return text(integer(identifier + 0x20))


class SearchEntry(gdb.Breakpoint):
    def stop(self):
        global pending
        try:
            if pending is not None:
                raise ValueError('unexpected nested scratch search')
            start, end = reg('edi'), reg('esi')
            current = integer(profile['current'], 4)
            if not (0 <= start < 5 and 0 <= end < 5) or not 0 <= current < 5:
                raise ValueError('unsupported peephole window')
            insn = integer(profile['window'] + 16 * ((current + start) % 5))
            pending = {'index': len(events), 'function': function_name(),
                       'uid': integer(insn + 8, 4), 'from': start, 'to': end,
                       'class': text(reg('rdx')), 'mode': reg('ecx'),
                       'before': integer(profile['state'], 4),
                       'allocation_order': [integer(profile['order'] + 4 * i, 4) for i in range(profile['register_count'])],
                       'reserved': integer(reg('r8')), 'reg_set': reg('r8'),
                       'caller': integer(reg('rsp'))}
            return False
        except (gdb.error, ValueError, OSError) as error:
            errors.append(str(error))
            return True


class SearchReturn(gdb.Breakpoint):
    def stop(self):
        global pending
        try:
            if pending is None:
                raise ValueError('scratch return without entry')
            result = reg('rax')
            number = integer(result + 8, 4) if result else None
            pending.update(success=bool(result), register=number,
                           register_name=text(integer(profile['reg_names'] + 8 * number)) if result else None,
                           after=integer(profile['state'], 4))
            del pending['reg_set']
            events.append(pending)
            pending = None
            return False
        except (gdb.error, ValueError, OSError) as error:
            errors.append(str(error))
            return True


def finish(event):
    if pending is not None:
        errors.append('unfinished scratch search')
    Path(config['events']).write_text(json.dumps({'events': events, 'errors': errors,
                                                'exit_code': getattr(event, 'exit_code', None)}, indent=2) + '\n')


SearchEntry('*' + hex(profile['entry']), internal=True)
SearchReturn('*' + hex(profile['return']), internal=True)
gdb.events.exited.connect(finish)
