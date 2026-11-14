"""x87 FPU lifting. The register stack lives in guest memory (FPU_BASE) with TOP in a wasm global, so
calls/returns that pass values in st(0) work without any static stack tracking. Values are f64; the
80-bit extended precision of real x87 is not modelled (rounding can differ in the last bit)."""
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from lift import Unsupported
from lift_ext import FPU_BASE

TOP = '(local.get $ftop)'


def sti(i):
    return (f'(i32.add (local.get $fpbase) (i32.shl (i32.and (i32.add {TOP} (i32.const {i})) '
            f'(i32.const 7)) (i32.const 3)))')


def ld(i):
    return f'(f64.load {sti(i)})'


def st_set(i, v):
    return f'(f64.store {sti(i)} {v})'


def push(v):
    return ['(local.set $tf %s)' % v, '(local.set $ftop (i32.and (i32.sub %s (i32.const 1)) (i32.const 7)))' % TOP,
            st_set(0, '(local.get $tf)')]


POP = '(local.set $ftop (i32.and (i32.add %s (i32.const 1)) (i32.const 7)))' % TOP


def sreg(self, op):
    """Index of an st(i) register operand, else None."""
    if op.type == X86_OP_REG:
        n = self.md.reg_name(op.reg)
        if n.startswith('st'):
            return 0 if n == 'st' else int(n.strip('st()'))
    return None


def round_rc(v):
    rc = '(i32.and (i32.shr_u (global.get $g_fcw) (i32.const 10)) (i32.const 3))'
    return (f'(select (select (f64.nearest {v}) (f64.floor {v}) (i32.eqz {rc})) '
            f'(select (f64.ceil {v}) (f64.trunc {v}) (i32.eq {rc} (i32.const 2))) '
            f'(i32.lt_u {rc} (i32.const 2)))')


def fpu_insn(self, i, mn, ops):
    o = []
    msz = ops[0].size if ops and ops[0].type == X86_OP_MEM else 0

    def src_f64(op):
        k = sreg(self, op)
        if k is not None:
            return ld(k)
        a = self.addr(op)
        return f'(f64.promote_f32 (f32.load {a}))' if op.size == 4 else f'(f64.load {a})'

    if mn in ('fld',):
        if ops[0].type == X86_OP_MEM and ops[0].size == 10:
            return push(f'(call $ld80 {self.addr(ops[0])})'), None
        return push(src_f64(ops[0])), None
    if mn == 'fld1':
        return push('(f64.const 1)'), None
    if mn == 'fldz':
        return push('(f64.const 0)'), None
    consts = {'fldpi': 3.141592653589793, 'fldl2e': 1.4426950408889634, 'fldln2': 0.6931471805599453,
              'fldl2t': 3.321928094887362, 'fldlg2': 0.3010299956639812}
    if mn in consts:
        return push(f'(f64.const {consts[mn]!r})'), None
    if mn in ('fild',):
        a = self.addr(ops[0])
        v = {2: f'(f64.convert_i32_s (i32.load16_s {a}))', 4: f'(f64.convert_i32_s (i32.load {a}))',
             8: f'(f64.convert_i64_s (i64.load {a}))'}[ops[0].size]
        return push(v), None
    if mn in ('fst', 'fstp'):
        k = sreg(self, ops[0])
        if k is not None:
            o.append(st_set(k, ld(0)))
        else:
            a = self.addr(ops[0])
            if ops[0].size == 4:
                o.append(f'(f32.store {a} (f32.demote_f64 {ld(0)}))')
            elif ops[0].size == 8:
                o.append(f'(f64.store {a} {ld(0)})')
            else:
                o.append(f'(call $st80 {a} {ld(0)})')
        if mn == 'fstp':
            o.append(POP)
        return o, None
    if mn in ('fist', 'fistp', 'fisttp'):
        a = self.addr(ops[0])
        sz = ops[0].size
        v = ld(0) if mn == 'fisttp' else round_rc(ld(0))
        if mn == 'fisttp':
            v = f'(f64.trunc {v})'
        o.append(f'(local.set $tf {v})')
        T = '(local.get $tf)'
        if sz == 8:
            o.append(f'(i64.store {a} (select (i64.trunc_sat_f64_s {T}) (i64.const 0x8000000000000000) '
                     f'(i32.and (f64.ge {T} (f64.const -9223372036854775808)) (f64.lt {T} (f64.const 9223372036854775808)))))')
        elif sz == 4:
            o.append(f'(i32.store {a} (select (i32.trunc_sat_f64_s {T}) (i32.const 0x80000000) '
                     f'(i32.and (f64.ge {T} (f64.const -2147483648)) (f64.lt {T} (f64.const 2147483648)))))')
        else:
            o.append(f'(i32.store16 {a} (select (i32.trunc_sat_f64_s {T}) (i32.const 0x8000) '
                     f'(i32.and (f64.ge {T} (f64.const -32768)) (f64.lt {T} (f64.const 32768)))))')
        if mn != 'fist':
            o.append(POP)
        return o, None
    arith = {'fadd': 'add', 'fsub': 'sub', 'fmul': 'mul', 'fdiv': 'div', 'fsubr': 'sub', 'fdivr': 'div',
             'faddp': 'add', 'fsubp': 'sub', 'fmulp': 'mul', 'fdivp': 'div', 'fsubrp': 'sub', 'fdivrp': 'div',
             'fiadd': 'add', 'fisub': 'sub', 'fimul': 'mul', 'fidiv': 'div', 'fisubr': 'sub', 'fidivr': 'div'}
    if mn in arith:
        op = arith[mn]
        rev = mn.endswith('r') or mn.endswith('rp')
        popit = mn.endswith('p')
        if mn.startswith('fi'):
            a = self.addr(ops[0])
            rhs = (f'(f64.convert_i32_s (i32.load16_s {a}))' if ops[0].size == 2 else f'(f64.convert_i32_s (i32.load {a}))')
            dst, lhs = 0, ld(0)
        elif ops and ops[0].type == X86_OP_MEM:
            dst, lhs, rhs = 0, ld(0), src_f64(ops[0])
        else:
            if len(ops) == 2:
                d, s = sreg(self, ops[0]), sreg(self, ops[1])
            elif len(ops) == 1:
                # capstone prints one operand: it is the destination for the popping forms, else the source
                d, s = (sreg(self, ops[0]), 0) if popit else (0, sreg(self, ops[0]))
            else:
                d, s = 1, 0  # bare `faddp` == faddp st(1), st(0)
            dst, lhs, rhs = d, ld(d), ld(s)
        a_, b_ = (rhs, lhs) if rev else (lhs, rhs)
        o.append(st_set(dst, f'(f64.{op} {a_} {b_})'))
        if popit:
            o.append(POP)
        return o, None
    if mn == 'fxch':
        k = sreg(self, ops[-1]) if ops else 1
        o.append(f'(local.set $tf {ld(0)})')
        o.append(st_set(0, ld(k)))
        o.append(st_set(k, '(local.get $tf)'))
        return o, None
    if mn == 'fchs':
        return [st_set(0, f'(f64.neg {ld(0)})')], None
    if mn == 'fabs':
        return [st_set(0, f'(f64.abs {ld(0)})')], None
    if mn == 'fsqrt':
        return [st_set(0, f'(f64.sqrt {ld(0)})')], None
    if mn == 'frndint':
        return [st_set(0, round_rc(ld(0)))], None
    if mn in ('fsin', 'fcos'):
        return [st_set(0, f'(call $m_{mn[1:]} {ld(0)})'), '(local.set $cf (i32.const 0))'][:1], None
    if mn == 'fsincos':
        # For |ST(0)| >= 2^63 x87 leaves the stack untouched and sets C2.
        # Within range, sin replaces ST(0) and cos is pushed above it.
        o.append(f'(local.set $tg {ld(0)})')
        success = [st_set(0, '(call $m_sin (local.get $tg))')]
        success += push('(call $m_cos (local.get $tg))')
        success.append('(global.set $g_fsw (i32.and (global.get $g_fsw) (i32.const 0xfbff)))')
        o.append('(if (f64.ge (f64.abs (local.get $tg)) (f64.const 9223372036854775808)) '
                 '(then (global.set $g_fsw (i32.or (global.get $g_fsw) (i32.const 0x400)))) '
                 '(else ' + ' '.join(success) + '))')
        return o, None
    if mn == 'fpatan':
        return [st_set(1, f'(call $m_atan2 {ld(1)} {ld(0)})'), POP], None
    if mn == 'fyl2x':
        return [st_set(1, f'(f64.mul {ld(1)} (call $m_log2 {ld(0)}))'), POP], None
    if mn == 'f2xm1':
        return [st_set(0, f'(call $m_exp2m1 {ld(0)})')], None
    if mn == 'fscale':
        return [st_set(0, f'(f64.mul {ld(0)} (call $m_exp2 (f64.trunc {ld(1)})))')], None
    if mn in ('fprem', 'fprem1'):
        return [st_set(0, f'(call $m_fmod {ld(0)} {ld(1)})'), '(global.set $g_fsw (i32.and (global.get $g_fsw) (i32.const 0xfaff)))'], None
    if mn in ('fcom', 'fcomp', 'fcompp', 'fucom', 'fucomp', 'fucompp', 'ficom', 'ficomp'):
        if mn.startswith('fi'):
            a = self.addr(ops[0])
            rhs = f'(f64.convert_i32_s (i32.load16_s {a}))' if ops[0].size == 2 else f'(f64.convert_i32_s (i32.load {a}))'
        elif mn.endswith('pp'):
            rhs = ld(1)
        elif not ops:
            rhs = ld(1)
        elif ops[0].type == X86_OP_MEM:
            rhs = src_f64(ops[0])
        else:
            k = sreg(self, ops[-1])
            rhs = ld(k)
        o.append(f'(local.set $tf {ld(0)})')
        o.append(f'(local.set $tg {rhs})')
        gt, lt, eq = '(f64.gt (local.get $tf) (local.get $tg))', '(f64.lt (local.get $tf) (local.get $tg))', '(f64.eq (local.get $tf) (local.get $tg))'
        cc = (f'(select (i32.const 0) (select (i32.const 0x100) (select (i32.const 0x4000) (i32.const 0x4500) {eq}) {lt}) {gt})')
        o.append(f'(global.set $g_fsw (i32.or (i32.and (global.get $g_fsw) (i32.const 0xb8ff)) {cc}))')
        if mn.endswith('pp'):
            o += [POP, POP]
        elif mn.endswith('p'):
            o.append(POP)
        return o, None
    if mn in ('fcomi', 'fcomip', 'fucomi', 'fucomip', 'fcompi', 'fucompi'):
        k = sreg(self, ops[-1])
        o.append(f'(local.set $tf {ld(0)})')
        o.append(f'(local.set $tg {ld(k)})')
        un = '(i32.eqz (f64.eq (local.get $tf) (local.get $tf)))'
        un2 = '(i32.eqz (f64.eq (local.get $tg) (local.get $tg)))'
        o.append(f'(local.set $tx (i32.or {un} {un2}))')
        o.append('(local.set $pf (local.get $tx))')
        o.append(f'(local.set $zf (i32.or (local.get $tx) (f64.eq (local.get $tf) (local.get $tg))))')
        o.append(f'(local.set $cf (i32.or (local.get $tx) (f64.lt (local.get $tf) (local.get $tg))))')
        o.append('(local.set $of (i32.const 0))')
        o.append('(local.set $sf (i32.const 0))')
        if mn in ('fcomip', 'fucomip', 'fcompi', 'fucompi'):
            o.append(POP)
        return o, None
    if mn == 'ftst':
        o.append(f'(local.set $tf {ld(0)})')
        gt, lt, eq = '(f64.gt (local.get $tf) (f64.const 0))', '(f64.lt (local.get $tf) (f64.const 0))', '(f64.eq (local.get $tf) (f64.const 0))'
        cc = (f'(select (i32.const 0) (select (i32.const 0x100) (select (i32.const 0x4000) (i32.const 0x4500) {eq}) {lt}) {gt})')
        o.append(f'(global.set $g_fsw (i32.or (i32.and (global.get $g_fsw) (i32.const 0xb8ff)) {cc}))')
        return o, None
    if mn in ('fnstsw', 'fstsw'):
        sw = f'(i32.or (i32.and (global.get $g_fsw) (i32.const 0xc7ff)) (i32.shl {TOP} (i32.const 11)))'
        if ops[0].type == X86_OP_REG:
            return [self.rset('ax', sw)], None
        return [f'(i32.store16 {self.addr(ops[0])} {sw})'], None
    if mn in ('fnstcw', 'fstcw'):
        return [f'(i32.store16 {self.addr(ops[0])} (global.get $g_fcw))'], None
    if mn == 'fldcw':
        return [f'(global.set $g_fcw (i32.load16_u {self.addr(ops[0])}))'], None
    if mn in ('fninit', 'finit'):
        return ['(local.set $ftop (i32.const 0))', '(global.set $g_fcw (i32.const 0x37f))', '(global.set $g_fsw (i32.const 0))'], None
    if mn in ('fnstenv', 'fstenv'):
        a = self.addr(ops[0])
        sw = f'(i32.or (i32.and (global.get $g_fsw) (i32.const 0xc7ff)) (i32.shl {TOP} (i32.const 11)))'
        return [f'(i32.store {a} (i32.or (global.get $g_fcw) (i32.const 0xffff0000)))', f'(i32.store offset=4 {a} {sw})',
                f'(i32.store offset=8 {a} (i32.const 0xffffffff))', f'(i64.store offset=12 {a} (i64.const 0))',
                f'(i64.store offset=20 {a} (i64.const 0))'], None
    if mn == 'fldenv':
        a = self.addr(ops[0])
        return [f'(global.set $g_fcw (i32.and (i32.load {a}) (i32.const 0xffff)))',
                f'(global.set $g_fsw (i32.and (i32.load offset=4 {a}) (i32.const 0xc7ff)))',
                f'(local.set $ftop (i32.and (i32.shr_u (i32.load offset=4 {a}) (i32.const 11)) (i32.const 7)))'], None
    if mn.startswith('fcmov'):
        cc = mn[5:]
        c = {'b': '(local.get $cf)', 'e': '(local.get $zf)', 'be': '(i32.or (local.get $cf) (local.get $zf))', 'u': '(local.get $pf)',
             'nb': '(i32.eqz (local.get $cf))', 'ne': '(i32.eqz (local.get $zf))',
             'nbe': '(i32.eqz (i32.or (local.get $cf) (local.get $zf)))', 'nu': '(i32.eqz (local.get $pf))'}[cc]
        k = sreg(self, ops[-1])
        return [f'(if {c} (then {st_set(0, ld(k))}))'], None
    if mn == 'fptan':
        return [st_set(0, f'(call $m_tan {ld(0)})')] + push('(f64.const 1)'), None
    if mn == 'fxam':
        v = ld(0)
        o.append(f'(local.set $tf {v})')
        T = '(local.get $tf)'
        cls = (f'(select (i32.const 0x500) (select (i32.const 0x4000) (i32.const 0x400) (f64.eq {T} (f64.const 0))) '
               f'(f64.ne {T} {T}))')
        cls = (f'(select (i32.const 0x100) (select (i32.const 0x500) (select (i32.const 0x4000) (i32.const 0x400) (f64.eq {T} (f64.const 0))) '
               f'(f64.eq (f64.abs {T}) (f64.const inf))) (f64.ne {T} {T}))')
        sign = f'(select (i32.const 0x200) (i32.const 0) (i32.shr_u (i32.wrap_i64 (i64.shr_u (i64.reinterpret_f64 {T}) (i64.const 63))) (i32.const 0)))'
        o.append(f'(global.set $g_fsw (i32.or (i32.and (global.get $g_fsw) (i32.const 0xb8ff)) (i32.or {cls} {sign})))')
        return o, None
    if mn in ('ffree', 'ffreep', 'fnop'):
        return [], None
    if mn == 'fincstp':
        return [POP], None
    if mn == 'fdecstp':
        return ['(local.set $ftop (i32.and (i32.sub %s (i32.const 1)) (i32.const 7)))' % TOP], None
    raise Unsupported(mn)
