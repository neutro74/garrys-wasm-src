"""Shared-memory x86 atomic operations using WebAssembly atomic RMWs.

Wasm requires naturally aligned atomic addresses; an unaligned x86 atomic traps
rather than silently becoming a non-atomic operation.
"""
from capstone.x86 import X86_OP_MEM
from lift import MASK, SIGN, Unsupported


def atomic_insn(f, i, mn, ops):
    if mn == 'xchg' and ops[1].type == X86_OP_MEM:
        s, d = ops
    else:
        d, *rest = ops
        s = rest[0] if rest else None
    if d.type != X86_OP_MEM or d.size not in (1, 2, 4):
        raise Unsupported('atomic operand')
    size = d.size
    a = f.addr(d)
    def rmw(op):
        return f'i32.atomic.rmw.{op}' if size == 4 else f'i32.atomic.rmw{size * 8}.{op}_u'

    if mn in ('xchg', 'xadd', 'add', 'sub', 'and', 'or', 'xor'):
        o = [f'(local.set $tb {f.load(s, size)})']
        op = {'xchg': 'xchg', 'xadd': 'add'}.get(mn, mn)
        o.append(f'(local.set $ta ({rmw(op)} {a} (local.get $tb)))')
        if mn == 'xchg':
            o.append(f.store(s, '(local.get $ta)'))
        elif mn in ('xadd', 'add', 'sub'):
            flags, _ = f.arith('add' if mn in ('xadd', 'add') else 'sub', i)
            o += flags[2:]  # old destination and source already captured
            if mn == 'xadd':
                o.append(f.store(s, '(local.get $ta)'))
        else:
            o += f.logic_flags(f'(i32.{mn} (local.get $ta) (local.get $tb))', size)
        return o, None
    if mn in ('inc', 'dec'):
        op = 'add' if mn == 'inc' else 'sub'
        lim = (1 << SIGN[size]) - 1 if mn == 'inc' else 1 << SIGN[size]
        return [f'(local.set $ta ({rmw(op)} {a} (i32.const 1)))',
                f'(local.set $tr (i32.and (i32.{op} (local.get $ta) (i32.const 1)) (i32.const {MASK[size]})))',
                f'(local.set $of (i32.eq (local.get $ta) (i32.const {lim})))',
                f.flags_zs('(local.get $tr)', size)], None
    if mn == 'cmpxchg':
        accumulator = {1: 'al', 2: 'ax', 4: 'eax'}[size]
        o = [f'(local.set $tb {f.rget(accumulator)})',
             f'(local.set $ta ({rmw("cmpxchg")} {a} (local.get $tb) {f.load(s, size)}))',
             f'(local.set $tr (i32.and (i32.sub (local.get $tb) (local.get $ta)) (i32.const {MASK[size]})))',
             '(local.set $cf (i32.lt_u (local.get $tb) (local.get $ta)))',
             f'(local.set $of (i32.and (i32.shr_u (i32.and (i32.xor (local.get $tb) (local.get $ta)) '
             f'(i32.xor (local.get $tb) (local.get $tr))) (i32.const {SIGN[size]})) (i32.const 1)))',
             f.flags_zs('(local.get $tr)', size),
             f'(if (i32.ne (local.get $tb) (local.get $ta)) (then {f.rset(accumulator, "(local.get $ta)")}))']
        return o, None
    raise Unsupported('shared-memory ' + i.mnemonic)
