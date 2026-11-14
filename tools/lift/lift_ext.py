"""Extensions for lift.Func: rotates, bit ops, string ops, x87 (memory-backed stack) and SSE (v128 locals)."""
import os
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from capstone import x86_const as X
import lift
from lift import MASK, SIGN, Unsupported

FPU_BASE = 0x300  # 8 x f64 guest-memory slots; FSW/FCW/ftop live in wasm globals
XMM = [f'xmm{i}' for i in range(8)]


# CPUID.1:EDX feature bits to hide from the lifted module (set by build_dll --cpuid-clear-edx).
CPUID_CLEAR_EDX = 0


def xr(f, op):
    """Name of an xmm register operand, or None."""
    if op.type == X86_OP_REG:
        n = f.md.reg_name(op.reg)
        if n in XMM:
            return n
    return None


def lane0_f32(f, op, size=4):
    """f32 value of a scalar-float source operand (xmm lane 0 or m32)."""
    n = xr(f, op)
    if n:
        return f'(f32x4.extract_lane 0 (local.get ${n}))'
    return f'(f32.load {f.addr(op)})'


def lane0_f64(f, op):
    n = xr(f, op)
    if n:
        return f'(f64x2.extract_lane 0 (local.get ${n}))'
    return f'(f64.load {f.addr(op)})'


def vec_src(f, op):
    n = xr(f, op)
    if n:
        return f'(local.get ${n})'
    return f'(v128.load align=1 {f.addr(op)})'


def trunc_i32(x, ty, rnd=None):
    """x86 cvt(t)ss2si semantics: out-of-range / NaN -> 0x80000000."""
    v = f'(local.get $tf)' if ty == 'f64' else '(local.get $ts)'
    lo, hi = ('-2147483648.0', '2147483648.0')
    expr = x
    if rnd == 'nearest':
        expr = f'({ty}.nearest {x})'
    setter = f'(local.set $tf {expr})' if ty == 'f64' else f'(local.set $ts {expr})'
    return (setter,
            f'(select (i32.trunc_sat_{ty}_s {v}) (i32.const 0x80000000) '
            f'(i32.and ({ty}.ge {v} ({ty}.const {lo})) ({ty}.lt {v} ({ty}.const {hi}))))')


def ext_insn(self, i, mn, ops):
    o = []
    size = ops[0].size if ops else 4
    # ---------------- integer extras ----------------
    if mn in ('rol', 'ror'):
        d, c = ops
        sz = d.size
        bits = sz * 8
        cnt = f'(i32.const {c.imm & 31})' if c.type == X86_OP_IMM else f'(i32.and {self.load(c, 1)} (i32.const 31))'
        o.append(f'(local.set $tc {cnt})')
        o.append(f'(local.set $ta {self.load(d)})')
        o.append(f'(local.set $tb (i32.rem_u (local.get $tc) (i32.const {bits})))')
        a, b = '(local.get $ta)', '(local.get $tb)'
        if sz == 4:
            res = f'(i32.{"rotl" if mn == "rol" else "rotr"} {a} {b})'
        elif mn == 'rol':
            res = f'(i32.and (i32.or (i32.shl {a} {b}) (i32.shr_u {a} (i32.sub (i32.const {bits}) {b}))) (i32.const {MASK[sz]}))'
        else:
            res = f'(i32.and (i32.or (i32.shr_u {a} {b}) (i32.shl {a} (i32.sub (i32.const {bits}) {b}))) (i32.const {MASK[sz]}))'
        body = [f'(local.set $tr {res})', self.store(d, '(local.get $tr)')]
        if mn == 'rol':
            body.append('(local.set $tx (i32.and (local.get $tr) (i32.const 1)))')
            body.append('(local.set $cf (local.get $tx))')
            body.append(f'(local.set $of (i32.xor (i32.and (i32.shr_u (local.get $tr) (i32.const {SIGN[sz]})) (i32.const 1)) (local.get $tx)))')
        else:
            body.append(f'(local.set $cf (i32.and (i32.shr_u (local.get $tr) (i32.const {SIGN[sz]})) (i32.const 1)))')
            body.append(f'(local.set $of (i32.xor (i32.and (i32.shr_u (local.get $tr) (i32.const {SIGN[sz]})) (i32.const 1)) '
                        f'(i32.and (i32.shr_u (local.get $tr) (i32.const {SIGN[sz] - 1})) (i32.const 1))))')
        o.append('(if (local.get $tc) (then ' + ' '.join(body) + '))')
        return o, None
    if mn in ('shld', 'shrd') and size == 4:
        d, s, c = ops
        cnt = f'(i32.const {c.imm & 31})' if c.type == X86_OP_IMM else f'(i32.and {self.load(c, 1)} (i32.const 31))'
        o.append(f'(local.set $tc {cnt})')
        o.append(f'(local.set $ta {self.load(d)})')
        o.append(f'(local.set $tb {self.load(s)})')
        a, b, n = '(local.get $ta)', '(local.get $tb)', '(local.get $tc)'
        if mn == 'shld':
            res = f'(i32.or (i32.shl {a} {n}) (i32.shr_u {b} (i32.sub (i32.const 32) {n})))'
            cf = f'(i32.and (i32.shr_u {a} (i32.sub (i32.const 32) {n})) (i32.const 1))'
        else:
            res = f'(i32.or (i32.shr_u {a} {n}) (i32.shl {b} (i32.sub (i32.const 32) {n})))'
            cf = f'(i32.and (i32.shr_u {a} (i32.sub {n} (i32.const 1))) (i32.const 1))'
        body = [f'(local.set $tr {res})', f'(local.set $cf {cf})',
                '(local.set $of (i32.xor (i32.shr_u (local.get $tr) (i32.const 31)) (i32.shr_u (local.get $ta) (i32.const 31))))',
                self.flags_zs('(local.get $tr)', 4), self.store(d, '(local.get $tr)')]
        o.append('(if (local.get $tc) (then ' + ' '.join(body) + '))')
        return o, None
    if mn in ('bt', 'bts', 'btr', 'btc') and ops[0].type == X86_OP_MEM and ops[0].size == 4:
        d, b = ops
        if b.type == X86_OP_IMM:
            off, bit = self.addr(d), f'(i32.const {b.imm & 31})'
        else:
            o.append(f'(local.set $tc {self.load(b, 4)})')
            off = f'(i32.add {self.addr(d)} (i32.shl (i32.shr_s (local.get $tc) (i32.const 5)) (i32.const 2)))'
            bit = '(i32.and (local.get $tc) (i32.const 31))'
        o.append(f'(local.set $tb {bit})')
        o.append(f'(local.set $ta (i32.load {off}))')
        o.append('(local.set $cf (i32.and (i32.shr_u (local.get $ta) (local.get $tb)) (i32.const 1)))')
        m = '(i32.shl (i32.const 1) (local.get $tb))'
        nv = {'bts': f'(i32.or (local.get $ta) {m})', 'btr': f'(i32.and (local.get $ta) (i32.xor {m} (i32.const -1)))',
              'btc': f'(i32.xor (local.get $ta) {m})'}.get(mn)
        if nv:
            o.append(f'(i32.store {off} {nv})')
        return o, None
    if mn in ('rcl', 'rcr') and size == 4:
        d, c = ops
        cnt = f'(i32.const {c.imm & 31})' if c.type == X86_OP_IMM else f'(i32.and {self.load(c, 1)} (i32.const 31))'
        o.append(f'(local.set $tc {cnt})')
        o.append(f'(local.set $t64 (i64.or (i64.shl (i64.extend_i32_u (local.get $cf)) (i64.const 32)) (i64.extend_i32_u {self.load(d)})))')
        o.append('(local.set $tb (i32.rem_u (local.get $tc) (i32.const 33)))')
        n = '(i64.extend_i32_u (local.get $tb))'
        v = '(local.get $t64)'
        if mn == 'rcl':
            r = f'(i64.and (i64.or (i64.shl {v} {n}) (i64.shr_u {v} (i64.sub (i64.const 33) {n}))) (i64.const 0x1ffffffff))'
        else:
            r = f'(i64.and (i64.or (i64.shr_u {v} {n}) (i64.shl {v} (i64.sub (i64.const 33) {n}))) (i64.const 0x1ffffffff))'
        body = [f'(local.set $t64 {r})', self.store(d, '(i32.wrap_i64 (local.get $t64))'),
                '(local.set $of (i32.and (i32.xor (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 31))) (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 32)))) (i32.const 1)))' if mn == 'rcl' else
                '(local.set $of (i32.and (i32.xor (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 31))) (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 30)))) (i32.const 1)))',
                '(local.set $cf (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 32))))']
        o.append('(if (local.get $tb) (then ' + ' '.join(body) + '))')
        return o, None
    if mn in ('bt', 'bts', 'btr', 'btc') and ops[0].type == X86_OP_REG and size in (2, 4):
        d, b = ops
        bit = f'(i32.and {self.load(b) if b.type != X86_OP_IMM else "(i32.const %d)" % b.imm} (i32.const {size * 8 - 1}))'
        o.append(f'(local.set $tb {bit})')
        o.append(f'(local.set $ta {self.load(d)})')
        o.append('(local.set $cf (i32.and (i32.shr_u (local.get $ta) (local.get $tb)) (i32.const 1)))')
        m = '(i32.shl (i32.const 1) (local.get $tb))'
        if mn == 'bts':
            o.append(self.store(d, f'(i32.or (local.get $ta) {m})'))
        elif mn == 'btr':
            o.append(self.store(d, f'(i32.and (local.get $ta) (i32.xor {m} (i32.const -1)))'))
        elif mn == 'btc':
            o.append(self.store(d, f'(i32.xor (local.get $ta) {m})'))
        return o, None
    if mn in ('bsf', 'bsr') and size == 4:
        d, s = ops
        o.append(f'(local.set $ta {self.load(s)})')
        o.append('(local.set $zf (i32.eqz (local.get $ta)))')
        r = ('(i32.ctz (local.get $ta))' if mn == 'bsf' else
             '(i32.sub (i32.const 31) (i32.clz (local.get $ta)))')
        o.append(f'(if (local.get $ta) (then {self.store(d, r)}))')
        return o, None
    if mn == 'lahf':
        o.append(self.rset('ah', '(i32.or (i32.or (i32.or (local.get $cf) (i32.const 2)) (i32.or (i32.shl (local.get $pf) (i32.const 2)) '
                 '(i32.shl (local.get $zf) (i32.const 6)))) (i32.shl (local.get $sf) (i32.const 7)))'))
        return o, None
    if mn == 'sahf':
        o.append(f'(local.set $ta {self.rget("ah")})')
        o.append('(local.set $cf (i32.and (local.get $ta) (i32.const 1)))')
        o.append('(local.set $pf (i32.and (i32.shr_u (local.get $ta) (i32.const 2)) (i32.const 1)))')
        o.append('(local.set $zf (i32.and (i32.shr_u (local.get $ta) (i32.const 6)) (i32.const 1)))')
        o.append('(local.set $sf (i32.and (i32.shr_u (local.get $ta) (i32.const 7)) (i32.const 1)))')
        return o, None
    if mn == 'cld':
        return ['(global.set $g_df (i32.const 0))'], None
    if mn == 'std':
        return ['(global.set $g_df (i32.const 1))'], None
    if mn == 'pushal':
        # PUSHAD stores the original ESP, not its value after the first pushes.
        o.append('(local.set $ta (local.get $esp))')
        for reg in ('eax', 'ecx', 'edx', 'ebx', None, 'ebp', 'esi', 'edi'):
            o += self.push(f'(local.get ${reg})' if reg else '(local.get $ta)')
        return o, None
    if mn == 'popal':
        # POPAD skips the saved ESP slot.
        for reg in ('edi', 'esi', 'ebp', None, 'ebx', 'edx', 'ecx', 'eax'):
            if reg:
                o.append(f'(local.set ${reg} (i32.load (local.get $esp)))')
            o.append('(local.set $esp (i32.add (local.get $esp) (i32.const 4)))')
        return o, None
    if mn == 'xlatb':
        addr = f'(i32.add {self.rget("bx" if i.addr_size == 2 else "ebx")} {self.rget("al")})'
        if i.addr_size == 2:
            addr = f'(i32.and {addr} (i32.const 0xffff))'
        if i.prefix[1] == 0x64:  # FS override
            base = '(global.get $g_fsbase)' if self.shared_memory else f'(i32.const {self.fs_base})'
            addr = f'(i32.add {addr} {base})'
        elif i.prefix[1] == 0x65:  # GS base is not modelled
            raise Unsupported('xlatb gs')
        return [self.rset('al', f'(i32.load8_u {addr})')], None
    if mn in ('wait', 'fwait', 'pause', 'fnclex', 'emms', 'femms', 'prefetcht0', 'prefetchnta', 'prefetcht1', 'prefetcht2', 'mfence', 'lfence', 'sfence'):
        return o, None
    if mn == 'xadd' and size == 4:
        d, s = ops
        o.append(f'(local.set $ta {self.load(d)})')
        o.append(f'(local.set $tb {self.load(s)})')
        pre, res = self.arith('add', i)
        o += pre
        o.append(self.store(s, '(local.get $ta)'))
        o.append(self.store(d, res))
        return o, None
    if mn == 'cmpxchg' and size == 4:
        d, s = ops
        o.append(f'(local.set $ta {self.load(d)})')
        o.append('(local.set $tb (local.get $eax))')
        o.append('(local.set $tr (i32.sub (local.get $eax) (local.get $ta)))')
        o.append('(local.set $cf (i32.lt_u (local.get $eax) (local.get $ta)))')
        o.append('(local.set $of (i32.and (i32.shr_u (i32.and (i32.xor (local.get $eax) (local.get $ta)) (i32.xor (local.get $eax) (local.get $tr))) (i32.const 31)) (i32.const 1)))')
        o.append(self.flags_zs('(local.get $tr)', 4))
        o.append(f'(if (i32.eqz (local.get $tr)) (then {self.store(d, self.load(s))}) (else (local.set $eax (local.get $ta))))')
        return o, None
    if mn in ('movsb', 'movsw', 'movsd', 'stosb', 'stosw', 'stosd', 'lodsb', 'lodsw', 'lodsd', 'scasb', 'scasw', 'scasd', 'cmpsb', 'cmpsw', 'cmpsd') and not any(xr(self, o) for o in ops):
        return string_op(self, i, mn, '')
    for pfx in ('rep ', 'repne ', 'repe '):
        if mn.startswith(pfx):
            return string_op(self, i, mn[len(pfx):], pfx.strip())
    if mn == 'cpuid':
        feature_edx = 0x07808111 & ~CPUID_CLEAR_EDX
        def pick(l0, l1):
            return (f'(select (i32.const {l0}) (select (i32.const {l1}) (i32.const 0) (i32.eq (local.get $ta) (i32.const 1))) '
                    f'(i32.eqz (local.get $ta)))')
        o.append('(local.set $ta (local.get $eax))')
        o.append(f'(local.set $eax (select (i32.const 1) (select (i32.const 0x000306a9) (i32.const 0) (i32.eq (local.get $ta) (i32.const 1))) (i32.eqz (local.get $ta))))')
        o.append(f'(local.set $ebx {pick(0x756e6547, 0x00000800)})')
        o.append(f'(local.set $ecx {pick(0x6c65746e, 0)})')
        o.append(f'(local.set $edx {pick(0x49656e69, feature_edx)})')
        return o, None
    if mn == 'rdtsc':
        return ['(local.set $t64 (call $rdtsc))',
                '(local.set $eax (i32.wrap_i64 (local.get $t64)))',
                '(local.set $edx (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 32))))'], None
    if mn == 'xgetbv':
        return ['(local.set $eax (i32.const 3))', '(local.set $edx (i32.const 0))'], None
    if mn == 'popfd':
        v, adj = self.pop()
        return [f'(local.set $ta {v})', adj, '(global.set $g_efl (i32.and (local.get $ta) (i32.const 0xfffff72a)))', '(local.set $cf (i32.and (local.get $ta) (i32.const 1)))',
                '(local.set $pf (i32.and (i32.shr_u (local.get $ta) (i32.const 2)) (i32.const 1)))',
                '(local.set $zf (i32.and (i32.shr_u (local.get $ta) (i32.const 6)) (i32.const 1)))',
                '(local.set $sf (i32.and (i32.shr_u (local.get $ta) (i32.const 7)) (i32.const 1)))',
                '(local.set $of (i32.and (i32.shr_u (local.get $ta) (i32.const 11)) (i32.const 1)))'], None
    if mn == 'stmxcsr':
        return [f'(i32.store {self.addr(ops[0])} (i32.const 0x1f80))'], None
    if mn == 'ldmxcsr':
        return [], None
    if mn == 'pushfd':
        fl = ('(i32.or (i32.or (i32.or (local.get $cf) (i32.const 2)) (i32.or (i32.shl (local.get $pf) (i32.const 2)) (i32.shl (local.get $zf) (i32.const 6)))) '
              '(i32.or (i32.or (i32.shl (local.get $sf) (i32.const 7)) (i32.shl (local.get $of) (i32.const 11))) (global.get $g_efl)))')
        return self.push(fl), None
    # ---------------- x87 ----------------
    if mn.startswith('f'):
        from lift_fpu import fpu_insn
        return fpu_insn(self, i, mn, ops)
    # ---------------- SSE ----------------
    from lift_sse import sse_insn
    return sse_insn(self, i, mn, ops)


def string_op(self, i, mn, rep):
    sz = {'b': 1, 'w': 2, 'd': 4}[mn[-1]]
    base = mn[:-1]
    ld = {1: 'i32.load8_u', 2: 'i32.load16_u', 4: 'i32.load'}[sz]
    st = {1: 'i32.store8', 2: 'i32.store16', 4: 'i32.store'}[sz]
    acc = {1: '(i32.and (local.get $eax) (i32.const 0xff))', 2: '(i32.and (local.get $eax) (i32.const 0xffff))', 4: '(local.get $eax)'}[sz]
    step = f'(select (i32.const {-sz}) (i32.const {sz}) (global.get $g_df))'
    body = []
    flagcmp = False
    if base == 'movs':
        body = [f'({st} (local.get $edi) ({ld} (local.get $esi)))',
                f'(local.set $esi (i32.add (local.get $esi) {step}))', f'(local.set $edi (i32.add (local.get $edi) {step}))']
    elif base == 'stos':
        body = [f'({st} (local.get $edi) {acc})', f'(local.set $edi (i32.add (local.get $edi) {step}))']
    elif base == 'lods':
        sv = {1: self.rset('al', f'({ld} (local.get $esi))'), 2: self.rset('ax', f'({ld} (local.get $esi))'), 4: f'(local.set $eax ({ld} (local.get $esi)))'}[sz]
        body = [sv, f'(local.set $esi (i32.add (local.get $esi) {step}))']
    elif base in ('scas', 'cmps'):
        flagcmp = True
        a = acc if base == 'scas' else f'({ld} (local.get $esi))'
        b = f'({ld} (local.get $edi))'
        m = MASK[sz]
        body = [f'(local.set $ta {a})', f'(local.set $tb {b})',
                f'(local.set $tr (i32.and (i32.sub (local.get $ta) (local.get $tb)) (i32.const {m})))',
                '(local.set $cf (i32.lt_u (local.get $ta) (local.get $tb)))',
                f'(local.set $of (i32.and (i32.shr_u (i32.and (i32.xor (local.get $ta) (local.get $tb)) (i32.xor (local.get $ta) (local.get $tr))) (i32.const {SIGN[sz]})) (i32.const 1)))',
                self.flags_zs('(local.get $tr)', sz),
                f'(local.set $edi (i32.add (local.get $edi) {step}))']
        if base == 'cmps':
            body.append(f'(local.set $esi (i32.add (local.get $esi) {step}))')
    else:
        raise Unsupported(mn)
    if not rep:
        return body, None
    # rep loop: exact semantics, ecx counts down, repe/repne test ZF after each compare.
    cond_exit = ''
    if rep == 'repe' or (rep == 'rep' and flagcmp):
        cond_exit = '(br_if $done (i32.eqz (local.get $zf)))'
    elif rep == 'repne':
        cond_exit = '(br_if $done (local.get $zf))'
    if os.environ.get('LIFT_STRING_TRACE'):
        body = body + [f'(call $trace (i32.const {i.address}))']
    loop = (f'(block $done (loop $again (br_if $done (i32.eqz (local.get $ecx))) ' + ' '.join(body) +
            ' (local.set $ecx (i32.sub (local.get $ecx) (i32.const 1))) ' + cond_exit + ' (br $again)))')
    # Large forward `rep movs`/`rep stos` (memcpy/memset inlined by the compiler) use bulk memory operations instead of
    # a per-element loop. Same result: forward copies only when the ranges cannot overlap in the direction that matters
    # (a forward element copy with dst inside (src, src+len) replicates data, memory.copy would not); fills only when
    # the stored element is a repeated byte. Small counts stay in the loop, where a runtime call would cost more.
    if rep == 'rep' and base in ('movs', 'stos') and not os.environ.get('LIFT_STRING_TRACE') and not os.environ.get('LIFT_NO_BULK_STRING'):
        n = '(local.get $ta)'
        advance = (f'(local.set $edi (i32.add (local.get $edi) {n})) '
                   + (f'(local.set $esi (i32.add (local.get $esi) {n})) ' if base == 'movs' else '')
                   + '(local.set $ecx (i32.const 0))')
        if base == 'movs':
            ok = f'(i32.or (i32.le_u (local.get $edi) (local.get $esi)) (i32.ge_u (local.get $edi) (i32.add (local.get $esi) {n})))'
            op = f'(memory.copy (local.get $edi) (local.get $esi) {n})'
        else:
            ok = {1: '(i32.const 1)',
                  2: '(i32.eq (i32.and (local.get $eax) (i32.const 0xffff)) (i32.mul (i32.and (local.get $eax) (i32.const 255)) (i32.const 257)))',
                  4: '(i32.eq (local.get $eax) (i32.mul (i32.and (local.get $eax) (i32.const 255)) (i32.const 0x01010101)))'}[sz]
            op = f'(memory.fill (local.get $edi) (i32.and (local.get $eax) (i32.const 255)) {n})'
        fast = (f'(if (i32.and (i32.eqz (global.get $g_df)) (i32.ge_u (local.get $ecx) (i32.const {max(1, 64 // sz)}))) '
                f'(then (local.set $ta (i32.mul (local.get $ecx) (i32.const {sz}))) '
                f'(if {ok} (then {op} {advance} (br $fin)))))')
        return [f'(block $fin {fast} {loop})'], None
    return [loop], None


lift.Func.ext_insn = ext_insn
