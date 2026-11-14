#!/usr/bin/env python3
"""x86-32 -> WebAssembly lifter core (integer subset first; x87/SSE to follow).

Model
  * Guest address space == wasm linear memory (the image is relocated into it by the loader).
  * Each guest function becomes one wasm function. Guest registers live in wasm *globals* between
    functions and in *locals* inside one; they are synced around calls/returns.
  * Flags are computed eagerly into CF/ZF/SF/OF/PF locals (dead-flag elimination is a later pass).
  * Control flow uses direct branches and structured single-entry loops where proven safe.
    A `loop` + `br_table` dispatcher handles irreducible graphs and computed entries.
Output is WAT text; wasmtime (tests) or binaryen's wasm-as (production) assembles it.
"""
import os
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG
from capstone import x86_const as X

FULL = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi']
R16 = {'ax': 'eax', 'cx': 'ecx', 'dx': 'edx', 'bx': 'ebx', 'sp': 'esp', 'bp': 'ebp', 'si': 'esi', 'di': 'edi'}
R8L = {'al': 'eax', 'cl': 'ecx', 'dl': 'edx', 'bl': 'ebx'}
R8H = {'ah': 'eax', 'ch': 'ecx', 'dh': 'edx', 'bh': 'ebx'}
FLAGS = ['cf', 'zf', 'sf', 'of', 'pf']
XMMS = [f'xmm{i}' for i in range(8)]
# The x87 stack top and register-file base are function locals too (they were globals read on every st(i) access):
# ftop is synced like a register; fpbase is per thread and constant, so it is only loaded.
FPU = ['ftop']
STATE = set(FULL + FLAGS + XMMS + FPU + ['fpbase'])
SYNC_OUT, SYNC_IN = '(;sync-out;)', '(;sync-in;)'
# Around calls (with ABI_CALLS): flags are not stored (no compiled callee reads them) and callee-saved registers the
# callee provably preserves are not reloaded. The marker lists the registers to skip: (;sync-in-call ebx,esi;).
SYNC_OUT_CALL = '(;sync-out-call;)'
CALLEE_SAVED = ('ebx', 'esi', 'edi', 'ebp')
ABI_CALLS = os.environ.get('LIFT_ABI_CALLS') == '1'   # or build_dll.py --abi-calls
LEAF_CALLS = os.environ.get('LIFT_LEAF_CALLS') != '0'  # disable for controlled ABI-call comparisons
PRESERVES = {}             # function entry -> callee-saved registers it provably leaves unchanged (preserved_regs)
LEAF_SUMMARIES = {}        # same-image direct entry -> (global CPU reads, global CPU writes), proven call-free
MASK = {1: 0xff, 2: 0xffff, 4: 0xffffffff}
SIGN = {1: 7, 2: 15, 4: 31}

import re
SYNC_IN_CALL = re.compile(r'\(;sync-in-call ([a-z,]*);\)')
SYNC_LEAF = re.compile(r'\(;sync-leaf-(out|in|unwind) ([0-9a-f]+);\)')
FLAG_SET = re.compile(r'^\(local\.set \$(cf|zf|sf|of|pf) ')


class Unsupported(Exception):
    pass


def base_mnemonic(mn):
    """Normalize capstone's folded redundant prefixes to the real operation.

    MSVC emits `rep ret` (f3 c3) purely as a branch-prediction hint; the REP
    prefix is ignored by the CPU, so it is an ordinary RET. Capstone reports
    the combined mnemonic 'repz ret', which must not reach the generic REP
    string-op path.
    """
    return 'ret' if mn in ('rep ret', 'repz ret', 'repnz ret', 'repe ret') else mn

# Continuation addresses of calls to setjmp (the CRT's _setjmp3 starts with these stores into the jmp_buf). A later
# longjmp returns control to one of them from a deeper call, so the function containing it must be able to resume there
# and the dispatcher must turn a jump to it into an unwinding return.
SETJMP_PREFIX = bytes.fromhex('8b542404892a895a04897a08')
SETJMP_CONT = set()


class Func:
    """Lifts one guest function (set of basic blocks reachable from `entry`) to WAT."""

    def __init__(self, img, entry, fname=None, fs_base=0, keep_flags=False, entries=frozenset(), trace=False, region=None, export=False, chunk_of=None, cur_chunk=0, tidx=None, shared_memory=False):
        self.export = export
        self.chunk_of, self.cur_chunk, self.tidx = chunk_of, cur_chunk, tidx   # chunked linking: cross-chunk calls go via the shared table
        self.region = region   # (lo, hi) absolute: lift as one dispatcher function with every instruction an entry
        self.trace = trace
        self.shared_memory = shared_memory
        self.keep_flags = keep_flags
        self.entries = entries
        self.live = frozenset(FLAGS)
        self.img, self.entry = img, entry
        self.name = fname or f'f_{entry:08x}'
        self.md = Cs(CS_ARCH_X86, CS_MODE_32)
        self.md.detail = True
        self.fs_base = fs_base
        self.out = []
        self.ntmp = 0
        self.jtabs = {}
        self.unsupported = {}
        self.cur = 0

    # ---------- operand helpers (all produce folded WAT expressions) ----------
    def reg_name(self, r):
        return self.md.reg_name(r)

    def rget(self, name):
        if name in FULL:
            return f'(local.get ${name})'
        if name in R16:
            return f'(i32.and (local.get ${R16[name]}) (i32.const 0xffff))'
        if name in R8L:
            return f'(i32.and (local.get ${R8L[name]}) (i32.const 0xff))'
        if name in R8H:
            return f'(i32.and (i32.shr_u (local.get ${R8H[name]}) (i32.const 8)) (i32.const 0xff))'
        raise Unsupported(f'reg {name}')

    def rset(self, name, val):
        if name in FULL:
            return f'(local.set ${name} {val})'
        if name in R16:
            f = R16[name]
            return (f'(local.set ${f} (i32.or (i32.and (local.get ${f}) (i32.const 0xffff0000)) '
                    f'(i32.and {val} (i32.const 0xffff))))')
        if name in R8L:
            f = R8L[name]
            return (f'(local.set ${f} (i32.or (i32.and (local.get ${f}) (i32.const 0xffffff00)) '
                    f'(i32.and {val} (i32.const 0xff))))')
        if name in R8H:
            f = R8H[name]
            return (f'(local.set ${f} (i32.or (i32.and (local.get ${f}) (i32.const 0xffff00ff)) '
                    f'(i32.shl (i32.and {val} (i32.const 0xff)) (i32.const 8))))')
        raise Unsupported(f'reg {name}')

    def addr(self, op):
        m = op.mem
        parts = []
        if m.base:
            parts.append(self.rget(self.reg_name(m.base)))
        if m.index:
            idx = self.rget(self.reg_name(m.index))
            parts.append(idx if m.scale == 1 else f'(i32.mul {idx} (i32.const {m.scale}))')
        if m.disp or not parts:
            parts.append(f'(i32.const {m.disp & 0xffffffff})')
        if m.segment == X.X86_REG_FS:
            parts.append('(global.get $g_fsbase)' if self.shared_memory else f'(i32.const {self.fs_base})')
        elif m.segment not in (0, X.X86_REG_DS, X.X86_REG_ES, X.X86_REG_SS, X.X86_REG_CS):
            raise Unsupported('segment')
        e = parts[0]
        for p in parts[1:]:
            e = f'(i32.add {e} {p})'
        return e

    def load(self, op, size=None):
        size = size or op.size
        if op.type == X86_OP_REG:
            return self.rget(self.reg_name(op.reg))
        if op.type == X86_OP_IMM:
            return f'(i32.const {op.imm & MASK[size]})'
        if op.type == X86_OP_MEM:
            a = self.addr(op)
            return {1: f'(i32.load8_u {a})', 2: f'(i32.load16_u {a})', 4: f'(i32.load {a})'}[size]
        raise Unsupported('operand')

    def store(self, op, val, size=None):
        size = size or op.size
        if op.type == X86_OP_REG:
            return self.rset(self.reg_name(op.reg), val)
        if op.type == X86_OP_MEM:
            a = self.addr(op)
            return {1: f'(i32.store8 {a} {val})', 2: f'(i32.store16 {a} {val})', 4: f'(i32.store {a} {val})'}[size]
        raise Unsupported('store operand')

    def sext(self, v, size):
        if size == 4:
            return v
        sh = 32 - size * 8
        return f'(i32.shr_s (i32.shl {v} (i32.const {sh})) (i32.const {sh}))'

    # ---------- flags ----------
    def flags_zs(self, res, size):
        return (f'(local.set $zf (i32.eqz {res}))\n'
                f'(local.set $sf (i32.and (i32.shr_u {res} (i32.const {SIGN[size]})) (i32.const 1)))\n'
                f'(local.set $pf (i32.eqz (i32.and (i32.popcnt (i32.and {res} (i32.const 255))) (i32.const 1))))')

    def arith(self, kind, ins, carry_in=None):
        """add/sub/adc/sbb/cmp. Result masked; flags eager. Returns (stmts, result_local_expr)."""
        d, s = ins.operands[0], ins.operands[1]
        size = d.size
        a, b, r, c = '$ta', '$tb', '$tr', '$tc'
        m = MASK[size]
        pre = [f'(local.set {a} {self.load(d)})',
               f'(local.set {b} {self.sext(self.load(s, s.size), s.size) if s.type == X86_OP_IMM and s.size != size else self.load(s, size)})']
        if s.type == X86_OP_IMM:
            pre[1] = f'(local.set {b} (i32.const {s.imm & m}))'
        mk = f'(i32.const {m})'
        cin = carry_in or '(i32.const 0)'
        if kind == 'add':
            pre.append(f'(local.set {c} {cin})')
            pre.append(f'(local.set {r} (i32.and (i32.add (i32.add (local.get {a}) (local.get {b})) (local.get {c})) {mk}))')
            if carry_in is None:
                cf = f'(i32.lt_u (local.get {r}) (local.get {a}))'
            else:
                cf = (f'(select (i32.le_u (local.get {r}) (local.get {a})) (i32.lt_u (local.get {r}) (local.get {a})) (local.get {c}))')
            of = (f'(i32.and (i32.shr_u (i32.and (i32.xor (local.get {a}) (local.get {r})) '
                  f'(i32.xor (local.get {b}) (local.get {r}))) (i32.const {SIGN[size]})) (i32.const 1))')
        else:
            pre.append(f'(local.set {c} {cin})')
            pre.append(f'(local.set {r} (i32.and (i32.sub (i32.sub (local.get {a}) (local.get {b})) (local.get {c})) {mk}))')
            if carry_in is None:
                cf = f'(i32.lt_u (local.get {a}) (local.get {b}))'
            else:
                cf = (f'(select (i32.le_u (local.get {a}) (local.get {b})) (i32.lt_u (local.get {a}) (local.get {b})) (local.get {c}))')
            of = (f'(i32.and (i32.shr_u (i32.and (i32.xor (local.get {a}) (local.get {b})) '
                  f'(i32.xor (local.get {a}) (local.get {r}))) (i32.const {SIGN[size]})) (i32.const 1))')
        pre.append(f'(local.set $cf {cf})')
        pre.append(f'(local.set $of {of})')
        pre.append(self.flags_zs(f'(local.get {r})', size))
        return pre, f'(local.get {r})'

    def logic_flags(self, res, size):
        return [f'(local.set $tr {res})', '(local.set $cf (i32.const 0))', '(local.set $of (i32.const 0))',
                self.flags_zs('(local.get $tr)', size)]

    def cond(self, cc):
        z, c, s, o, p = ('(local.get $zf)', '(local.get $cf)', '(local.get $sf)', '(local.get $of)', '(local.get $pf)')
        t = {
            'e': z, 'ne': f'(i32.eqz {z})', 'b': c, 'ae': f'(i32.eqz {c})',
            'be': f'(i32.or {c} {z})', 'a': f'(i32.eqz (i32.or {c} {z}))',
            's': s, 'ns': f'(i32.eqz {s})', 'o': o, 'no': f'(i32.eqz {o})',
            'p': p, 'np': f'(i32.eqz {p})',
            'l': f'(i32.xor {s} {o})', 'ge': f'(i32.eqz (i32.xor {s} {o}))',
            'le': f'(i32.or {z} (i32.xor {s} {o}))', 'g': f'(i32.eqz (i32.or {z} (i32.xor {s} {o})))',
        }
        t['z'], t['nz'], t['c'], t['nc'], t['nae'], t['nb'], t['na'], t['nbe'] = (
            t['e'], t['ne'], t['b'], t['ae'], t['b'], t['ae'], t['be'], t['a'])
        t['nge'], t['nl'], t['nle'], t['ng'], t['pe'], t['po'] = (
            t['l'], t['ge'], t['g'], t['le'], t['p'], t['np'])
        return t[cc]

    # ---------- stack ----------
    def push(self, val):
        return [f'(local.set $esp (i32.sub (local.get $esp) (i32.const 4)))',
                f'(i32.store (local.get $esp) {val})']

    def pop(self):
        return '(i32.load (local.get $esp))', '(local.set $esp (i32.add (local.get $esp) (i32.const 4)))'

    # ---------- per-instruction semantics ----------
    def insn(self, i, nxt):
        """Returns (stmts, terminator) where terminator is None | ('jmp', tgt) | ('jcc', cond, tgt)
        | ('ret', n) | ('ijmp', expr) | ('call', tgt|expr, is_indirect)."""
        mn = base_mnemonic(i.mnemonic)
        locked = mn.startswith('lock ')
        if locked:
            mn = mn[5:]
        ops = i.operands
        o = []
        if mn == 'cmpxchg8b':
            a = self.addr(ops[0])
            expected = '(i64.or (i64.shl (i64.extend_i32_u (local.get $edx)) (i64.const 32)) (i64.extend_i32_u (local.get $eax)))'
            replacement = '(i64.or (i64.shl (i64.extend_i32_u (local.get $ecx)) (i64.const 32)) (i64.extend_i32_u (local.get $ebx)))'
            if locked and self.shared_memory:
                o.append(f'(local.set $t64 (i64.atomic.rmw.cmpxchg {a} {expected} {replacement}))')
            else:
                o.append(f'(local.set $t64 (i64.load {a}))')
            o.append(f'(local.set $tx (i64.eq (local.get $t64) {expected}))')
            o.append('(local.set $zf (local.get $tx))')
            success = '' if locked and self.shared_memory else f'(i64.store {a} {replacement})'
            o.append(f'(if (local.get $tx) (then {success}) (else '
                     '(local.set $eax (i32.wrap_i64 (local.get $t64))) '
                     '(local.set $edx (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 32))))))')
            return o, None
        if locked and self.shared_memory:
            from lift_atomic import atomic_insn
            return atomic_insn(self, i, mn, ops)
        if mn == 'xchg' and self.shared_memory and any(op.type == X86_OP_MEM for op in ops):
            from lift_atomic import atomic_insn
            return atomic_insn(self, i, mn, ops)
        if mn in ('nop', 'fnop'):
            return o, None
        if mn in ('int', 'int3', 'ud2', 'hlt'):
            return ['(unreachable)'], ('trap',)
        if mn == 'mov':
            o.append(self.store(ops[0], self.load(ops[1], ops[0].size)))
        elif mn in ('movzx',):
            o.append(self.store(ops[0], self.load(ops[1])))
        elif mn in ('movsx',):
            o.append(self.store(ops[0], self.sext(self.load(ops[1]), ops[1].size)))
        elif mn == 'lea':
            o.append(self.store(ops[0], self.addr_noseg(ops[1])))
        elif mn in ('add', 'sub', 'cmp', 'adc', 'sbb'):
            kind = 'add' if mn in ('add', 'adc') else 'sub'
            cin = '(local.get $cf)' if mn in ('adc', 'sbb') else None
            pre, res = self.arith(kind, i, cin)
            o += pre
            if mn != 'cmp':
                o.append(self.store(ops[0], res))
        elif mn in ('and', 'or', 'xor', 'test'):
            d = ops[0]
            sv = f'(i32.const {ops[1].imm & MASK[d.size]})' if ops[1].type == X86_OP_IMM else self.load(ops[1], d.size)
            op = {'and': 'and', 'test': 'and', 'or': 'or', 'xor': 'xor'}[mn]
            o += self.logic_flags(f'(i32.{op} {self.load(d)} {sv})', d.size)
            if mn != 'test':
                o.append(self.store(d, '(local.get $tr)'))
        elif mn in ('inc', 'dec'):
            d = ops[0]
            size = d.size
            o.append(f'(local.set $ta {self.load(d)})')
            op = 'add' if mn == 'inc' else 'sub'
            o.append(f'(local.set $tr (i32.and (i32.{op} (local.get $ta) (i32.const 1)) (i32.const {MASK[size]})))')
            lim = (1 << SIGN[size]) - 1 if mn == 'inc' else 1 << SIGN[size]
            o.append(f'(local.set $of (i32.eq (local.get $ta) (i32.const {lim})))')
            o.append(self.flags_zs('(local.get $tr)', size))
            o.append(self.store(d, '(local.get $tr)'))
        elif mn == 'neg':
            d = ops[0]
            size = d.size
            o.append(f'(local.set $ta {self.load(d)})')
            o.append(f'(local.set $tr (i32.and (i32.sub (i32.const 0) (local.get $ta)) (i32.const {MASK[size]})))')
            o.append('(local.set $cf (i32.ne (local.get $ta) (i32.const 0)))')
            o.append(f'(local.set $of (i32.eq (local.get $ta) (i32.const {1 << SIGN[size]})))')
            o.append(self.flags_zs('(local.get $tr)', size))
            o.append(self.store(d, '(local.get $tr)'))
        elif mn == 'not':
            d = ops[0]
            o.append(self.store(d, f'(i32.and (i32.xor {self.load(d)} (i32.const -1)) (i32.const {MASK[d.size]}))'))
        elif mn in ('shl', 'sal', 'shr', 'sar'):
            o += self.shift(i)
        elif mn == 'imul':
            o += self.imul(i)
        elif mn == 'push':
            v = self.load(ops[0], 4) if ops[0].type != X86_OP_IMM else f'(i32.const {ops[0].imm & 0xffffffff})'
            if ops[0].type == X86_OP_MEM:
                v = f'(i32.load {self.addr(ops[0])})'
            o.append(f'(local.set $ta {v})')   # evaluate before esp changes (push [esp+x], push esp)
            o += self.push('(local.get $ta)')
        elif mn == 'pop':
            v, adj = self.pop()
            o.append(f'(local.set $ta {v})')
            o.append(adj)  # esp adjusted before store so `pop [esp+..]` uses the new esp, like hardware
            o.append(self.store(ops[0], '(local.get $ta)'))
        elif mn == 'leave':
            o.append('(local.set $esp (local.get $ebp))')
            v, adj = self.pop()
            o.append(f'(local.set $ebp {v})')
            o.append(adj)
        elif mn == 'xchg':
            o.append(f'(local.set $ta {self.load(ops[0])})')
            o.append(f'(local.set $tb {self.load(ops[1])})')
            o.append(self.store(ops[0], '(local.get $tb)'))
            o.append(self.store(ops[1], '(local.get $ta)'))
        elif mn == 'cdq':
            o.append('(local.set $edx (i32.shr_s (local.get $eax) (i32.const 31)))')
        elif mn == 'cwde':
            o.append(f'(local.set $eax {self.sext(self.rget("ax"), 2)})')
        elif mn == 'cbw':
            o.append(self.rset('ax', self.sext(self.rget('al'), 1)))
        elif mn.startswith('set'):
            o.append(self.store(ops[0], self.cond(mn[3:])))
        elif mn.startswith('cmov'):
            o.append(self.store(ops[0], f'(select {self.load(ops[1], ops[0].size)} {self.load(ops[0])} {self.cond(mn[4:])})'))
        elif mn in ('div', 'idiv', 'mul'):
            o += self.muldiv(i)
        elif mn == 'bswap':
            r = self.reg_name(ops[0].reg)
            v = self.rget(r)
            o.append(f'(local.set $ta {v})')
            o.append(self.rset(r, '(i32.or (i32.or (i32.shl (local.get $ta) (i32.const 24)) '
                     '(i32.and (i32.shl (local.get $ta) (i32.const 8)) (i32.const 0xff0000))) '
                     '(i32.or (i32.and (i32.shr_u (local.get $ta) (i32.const 8)) (i32.const 0xff00)) '
                     '(i32.shr_u (local.get $ta) (i32.const 24))))'))
        elif mn == 'jmp':
            op = ops[0]
            if op.type == X86_OP_IMM:
                return o, ('jmp', op.imm)
            ents = self.jtabs.get(i.address)
            if ents:
                return o, ('jtab', ents, self.rget(self.reg_name(op.mem.index)))
            return o, ('ijmp', self.load(op, 4))
        elif mn == 'call':
            op = ops[0]
            if op.type == X86_OP_IMM:
                return o, ('call', op.imm, False)
            return o, ('call', self.load(op, 4), True)
        elif mn in ('ret', 'retn'):
            return o, ('ret', ops[0].imm if ops else 0)
        elif mn in ('jecxz', 'jcxz'):
            count = self.rget('cx') if mn == 'jcxz' else '(local.get $ecx)'
            return o, ('jcc', f'(i32.eqz {count})', ops[0].imm)
        elif mn in ('loop', 'loope', 'loopne'):
            # Address-size, not operand-size, selects CX versus ECX.
            reg = 'cx' if i.addr_size == 2 else 'ecx'
            mask = 0xffff if reg == 'cx' else 0xffffffff
            o.append(self.rset(reg, f'(i32.and (i32.sub {self.rget(reg)} (i32.const 1)) (i32.const {mask}))'))
            cond = f'(i32.ne {self.rget(reg)} (i32.const 0))'
            if mn == 'loope':
                cond = f'(i32.and {cond} (local.get $zf))'
            elif mn == 'loopne':
                cond = f'(i32.and {cond} (i32.eqz (local.get $zf)))'
            return o, ('jcc', cond, ops[0].imm)
        elif mn.startswith('j'):
            return o, ('jcc', self.cond(mn[1:]), ops[0].imm)
        else:
            return self.ext_insn(i, mn, ops)
        return o, None

    def prune(self, stmts):
        """Drop whole flag-write statements whose flag is dead after this instruction."""
        np = os.environ.get("LIFT_NOPRUNE")
        if np == "1" or (np == "region" and self.region) or (np == "func" and not self.region):
            return list(stmts)
        out = []
        for st in stmts:
            for line in st.split('\n'):
                m = FLAG_SET.match(line)
                if m and m.group(1) not in self.live and line.count('(') == line.count(')'):
                    continue
                out.append(line)
        return out

    def addr_noseg(self, op):
        return self.addr(op)

    def shift(self, i):
        d, c = i.operands[0], i.operands[1]
        size = d.size
        o = []
        cnt = f'(i32.and {self.load(c, 1) if c.type != X86_OP_IMM else "(i32.const %d)" % (c.imm & 0xff)} (i32.const 31))'
        o.append(f'(local.set $tc {cnt})')
        o.append(f'(local.set $ta {self.load(d)})')
        mn = i.mnemonic
        a = '(local.get $ta)'
        n = '(local.get $tc)'
        if mn in ('shl', 'sal'):
            res = f'(i32.and (i32.shl {a} {n}) (i32.const {MASK[size]}))'
            cf = f'(i32.and (i32.shr_u {a} (i32.sub (i32.const {size * 8}) {n})) (i32.const 1))'
            of = f'(i32.xor (i32.and (i32.shr_u (local.get $tr) (i32.const {SIGN[size]})) (i32.const 1)) {cf})'
        elif mn == 'shr':
            res = f'(i32.shr_u {a} {n})'
            cf = f'(i32.and (i32.shr_u {a} (i32.sub {n} (i32.const 1))) (i32.const 1))'
            of = f'(i32.and (i32.shr_u {a} (i32.const {SIGN[size]})) (i32.const 1))'
        else:  # sar
            sa = self.sext(a, size)
            res = f'(i32.and (i32.shr_s {sa} {n}) (i32.const {MASK[size]}))'
            cf = f'(i32.and (i32.shr_s {sa} (i32.sub {n} (i32.const 1))) (i32.const 1))'
            of = '(i32.const 0)'
        # count==0 leaves flags and destination untouched.
        body = [f'(local.set $tr {res})', f'(local.set $cf {cf})', f'(local.set $of {of})',
                self.flags_zs('(local.get $tr)', size), self.store(d, '(local.get $tr)')]
        o.append('(if (local.get $tc) (then ' + ' '.join(body) + '))')
        return o

    def imul(self, i):
        ops = i.operands
        o = []
        if len(ops) == 1:
            size = ops[0].size
            if size != 4:
                raise Unsupported('imul r/m8,16')
            o.append(f'(local.set $t64 (i64.mul (i64.extend_i32_s (local.get $eax)) (i64.extend_i32_s {self.load(ops[0])})))')
            o.append('(local.set $eax (i32.wrap_i64 (local.get $t64)))')
            o.append('(local.set $edx (i32.wrap_i64 (i64.shr_s (local.get $t64) (i64.const 32))))')
            o.append('(local.set $tx (i64.ne (local.get $t64) (i64.extend_i32_s (local.get $eax))))')
            o.append('(local.set $cf (local.get $tx))')
            o.append('(local.set $of (local.get $tx))')
            return o
        a, b = (ops[0], ops[1]) if len(ops) == 2 else (ops[1], ops[2])
        size = ops[0].size
        if size != 4:
            raise Unsupported('imul16')
        bv = f'(i32.const {b.imm & 0xffffffff})' if b.type == X86_OP_IMM else self.load(b)
        o.append(f'(local.set $t64 (i64.mul (i64.extend_i32_s {self.load(a)}) (i64.extend_i32_s {bv})))')
        o.append('(local.set $tr (i32.wrap_i64 (local.get $t64)))')
        o.append('(local.set $tx (i64.ne (local.get $t64) (i64.extend_i32_s (local.get $tr))))')
        o.append('(local.set $cf (local.get $tx))')
        o.append('(local.set $of (local.get $tx))')
        o.append(self.store(ops[0], '(local.get $tr)'))
        return o

    def muldiv(self, i):
        d = i.operands[0]
        if d.size != 4:
            raise Unsupported(i.mnemonic + ' 8/16')
        mn = i.mnemonic
        o = [f'(local.set $tb {self.load(d)})']
        if mn == 'mul':
            o.append('(local.set $t64 (i64.mul (i64.extend_i32_u (local.get $eax)) (i64.extend_i32_u (local.get $tb))))')
            o.append('(local.set $eax (i32.wrap_i64 (local.get $t64)))')
            o.append('(local.set $edx (i32.wrap_i64 (i64.shr_u (local.get $t64) (i64.const 32))))')
            o.append('(local.set $tx (i32.ne (local.get $edx) (i32.const 0)))')
            o.append('(local.set $cf (local.get $tx))')
            o.append('(local.set $of (local.get $tx))')
        else:
            sg = 's' if mn == 'idiv' else 'u'
            ext = '(i64.or (i64.shl (i64.extend_i32_u (local.get $edx)) (i64.const 32)) (i64.extend_i32_u (local.get $eax)))'
            vb = f'(i64.extend_i32_{sg} (local.get $tb))'
            o.append(f'(local.set $t64 {ext})')
            o.append(f'(local.set $eax (i32.wrap_i64 (i64.div_{sg} (local.get $t64) {vb})))')
            o.append(f'(local.set $edx (i32.wrap_i64 (i64.rem_{sg} (local.get $t64) {vb})))')
        return o

    # ---------- function assembly ----------
    def discover_region(self):
        lo, hi = self.region
        img = self.img
        insns, pc = {}, lo
        while pc < hi:
            ii = list(self.md.disasm(bytes(img.mem[pc - img.base:pc - img.base + 16]), pc, 1))
            if not ii:
                pc += 1
                continue
            insns[pc] = ii[0]
            pc += ii[0].size
        self.seen_insns = insns
        for pc, i in insns.items():
            if i.mnemonic == 'jmp' and i.operands[0].type == X86_OP_MEM:
                ents = self.jump_table(i, insns)
                if ents:
                    self.jtabs[pc] = ents
        return insns, sorted(insns)

    def discover_blocks(self):
        if self.region:
            return self.discover_region()
        img = self.img
        self.seen_insns = {}
        leaders = {self.entry}
        insns = {}
        work = [self.entry]
        seen = set()
        while work:
            pc = work.pop()
            while pc not in seen and img.in_text(pc):
                seen.add(pc)
                rva = pc - img.base
                ii = list(self.md.disasm(bytes(img.mem[rva:rva + 16]), pc, 1))
                if not ii:
                    break
                i = ii[0]
                insns[pc] = i
                self.seen_insns[pc] = i
                mn = base_mnemonic(i.mnemonic)
                if mn == 'jmp' and i.operands[0].type == X86_OP_IMM and i.operands[0].imm in self.entries and i.operands[0].imm != self.entry:
                    break   # tail call to another function
                if mn == 'jmp' and i.operands[0].type == X86_OP_IMM:
                    t = i.operands[0].imm
                    # A jump that leaves the function is a tail call, not a block edge.
                    leaders.add(t)
                    work.append(t)
                    break
                if mn == 'jmp' and i.operands[0].type == X86_OP_MEM:
                    ents = self.jump_table(i, insns)
                    if ents:
                        self.jtabs[pc] = ents
                        for t in ents:
                            leaders.add(t)
                            work.append(t)
                    break
                if mn == 'jmp' or mn in ('ret', 'retn', 'int3', 'int', 'ud2', 'hlt'):
                    break
                if mn == 'call':
                    leaders.add(pc + i.size)
                if (mn.startswith('j') or mn in ('loop', 'loope', 'loopne')) and i.operands[0].type == X86_OP_IMM:
                    leaders.add(i.operands[0].imm)
                    work.append(i.operands[0].imm)
                    leaders.add(pc + i.size)
                pc += i.size
        return insns, sorted(leaders)

    def jump_table(self, i, insns):
        """Resolve `jmp [table + idx*4]` into its list of block addresses (None if not a switch)."""
        m = i.operands[0].mem
        img = self.img
        if not (m.index and m.scale == 4 and not m.base):
            return None
        tbl = m.disp & 0xffffffff
        n = None
        # bound from a preceding `cmp idx, N` / `ja default`
        prev = [insns[a] for a in sorted(insns) if a < i.address][-14:]
        for a, b in zip(prev, prev[1:]):
            if a.mnemonic == 'cmp' and a.operands[1].type == X86_OP_IMM and b.mnemonic in ('ja', 'jae', 'jg', 'jnbe'):
                n = a.operands[1].imm + (1 if b.mnemonic in ('ja', 'jg', 'jnbe') else 0)
        ents = []
        for k in range(n if n is not None else 512):
            rva = tbl - img.base + 4 * k
            if rva not in img.reloc_set:
                break
            t = img.u32(rva)
            if not img.in_text(t):
                break
            ents.append(t)
        return ents or None

    CC_USES = {
        'e': 'z', 'ne': 'z', 'z': 'z', 'nz': 'z', 'b': 'c', 'ae': 'c', 'c': 'c', 'nc': 'c', 'nae': 'c', 'nb': 'c',
        'be': 'cz', 'a': 'cz', 'na': 'cz', 'nbe': 'cz', 's': 's', 'ns': 's', 'o': 'o', 'no': 'o', 'p': 'p', 'np': 'p',
        'pe': 'p', 'po': 'p', 'l': 'so', 'ge': 'so', 'nl': 'so', 'nge': 'so', 'le': 'zso', 'g': 'zso', 'ng': 'zso', 'nle': 'zso'}
    FL = {'c': 'cf', 'z': 'zf', 's': 'sf', 'o': 'of', 'p': 'pf'}
    ALLF = frozenset(FLAGS)

    def flag_ud(self, i):
        """(uses, kills) of the five flag locals for one instruction. Unknown => uses everything, kills nothing."""
        mn = base_mnemonic(i.mnemonic)
        if mn.startswith('lock '):
            mn = mn[5:]
        cc = None
        for pre in ('cmov', 'set', 'j'):
            if mn.startswith(pre) and mn[len(pre):] in self.CC_USES:
                cc = self.CC_USES[mn[len(pre):]]
        if cc is not None and mn not in ('jmp',):
            return frozenset(self.FL[c] for c in cc), frozenset()
        if mn.startswith('fcmov'):     # x87 conditional move reads CF/ZF/PF
            return frozenset(self.FL[c] for c in {'b': 'c', 'e': 'z', 'be': 'cz', 'u': 'p', 'nb': 'c', 'ne': 'z', 'nbe': 'cz', 'nu': 'p'}[mn[5:]]), frozenset()
        if mn in ('loope', 'loopne'):
            return frozenset({'zf'}), frozenset()
        if mn in ('jecxz', 'jcxz', 'jmp', 'call', 'ret', 'retn', 'push', 'pop', 'mov', 'movzx', 'movsx', 'lea', 'nop', 'leave',
                  'xchg', 'cdq', 'cwde', 'cbw', 'bswap', 'not', 'cld', 'wait', 'fwait', 'int3', 'ud2', 'hlt', 'pause'):
            return frozenset(), frozenset()
        if mn in ('adc', 'sbb'):
            return frozenset({'cf'}), self.ALLF
        if mn in ('add', 'sub', 'cmp', 'and', 'or', 'xor', 'test', 'neg', 'xadd', 'cmpxchg', 'imul', 'mul',
                  'comiss', 'ucomiss', 'comisd', 'ucomisd', 'fcomi', 'fcomip', 'fucomi', 'fucomip', 'fcompi', 'fucompi'):
            return frozenset(), self.ALLF
        if mn in ('inc', 'dec'):
            return frozenset(), frozenset({'zf', 'sf', 'of', 'pf'})
        if mn in ('shl', 'sal', 'shr', 'sar', 'rol', 'ror', 'shld', 'shrd'):
            return frozenset(), frozenset()      # count 0 leaves flags alone: may-define only
        if mn in ('rcl', 'rcr'):
            return frozenset({'cf'}), frozenset()
        if mn in ('bt', 'bts', 'btr', 'btc'):
            return frozenset(), frozenset({'cf'})
        if mn == 'cmpxchg8b':
            return frozenset(), frozenset({'zf'})
        if mn in ('bsf', 'bsr'):
            return frozenset(), frozenset()
        if mn == 'sahf':
            return frozenset(), frozenset({'cf', 'pf', 'zf', 'sf'})
        if mn.startswith(('rep', 'cmps', 'scas')):
            return self.ALLF, frozenset()
        if mn in ('div', 'idiv', 'lahf'):
            return (self.ALLF if mn == 'lahf' else frozenset()), frozenset()
        # pure SSE/x87 data movement and arithmetic never read the integer flags
        if mn.startswith('f') or mn.endswith(('ss', 'sd', 'ps', 'pd')) or mn.startswith(('p', 'mov', 'cvt', 'xorp', 'andp', 'orp', 'unpck', 'shufp', 'sqrt', 'rsqrt', 'rcp', 'min', 'max', 'lods', 'stos')):
            return frozenset(), frozenset()
        return self.ALLF, frozenset()

    def is_term(self, i):
        mn = base_mnemonic(i.mnemonic)
        return mn in ('jmp', 'ret', 'retn', 'call', 'int3', 'ud2', 'hlt', 'loop', 'loope', 'loopne') or mn.startswith('j')

    def is_setjmp(self, addr):
        off = addr - self.img.base
        return 0 <= off < len(self.img.mem) - 16 and bytes(self.img.mem[off:off + len(SETJMP_PREFIX)]) == SETJMP_PREFIX

    def lift(self, tail_targets=frozenset()):
        insns, leaders = self.discover_blocks()
        leaders = [l for l in leaders if l in insns]
        self.setjmp_conts = [] if self.region else [
            i.address + i.size for i in insns.values()
            if i.mnemonic == 'call' and i.operands and i.operands[0].type == X86_OP_IMM and self.is_setjmp(i.operands[0].imm)]
        SETJMP_CONT.update(self.setjmp_conts)
        if self.region:
            idx = {l: l - self.region[0] for l in leaders}      # pc code == byte offset into the region
        else:
            idx = {l: n for n, l in enumerate(leaders)}
        nblocks = len(leaders)
        # ---- pass 1: block layout + flag liveness ----
        blocks = []   # (start, [addrs], succs, ends_in_call_or_exit)
        for start in leaders:
            addrs, pc = [], start
            while True:
                i = insns.get(pc)
                if i is None:
                    break
                addrs.append(pc)
                pc += i.size
                if self.is_term(i) or (pc in idx and pc != start):
                    break
            last = insns.get(addrs[-1]) if addrs else None
            succ, barrier = [], True
            if last is not None:
                mn = base_mnemonic(last.mnemonic)
                nxt = addrs[-1] + last.size
                if mn == 'jmp':
                    if last.operands[0].type == X86_OP_IMM:
                        succ = [last.operands[0].imm] if last.operands[0].imm in idx else []
                    else:
                        succ = list(self.jtabs.get(last.address, []))
                    barrier = not succ
                elif mn.startswith('j') or mn in ('loop', 'loope', 'loopne'):
                    succ, barrier = [last.operands[0].imm, nxt], False
                elif mn == 'call':
                    succ, barrier = [nxt], True       # flags are dead across a call
                elif mn in ('ret', 'retn', 'int3', 'ud2', 'hlt'):
                    succ, barrier = [], True
                else:
                    succ, barrier = [nxt], False
            blocks.append((start, addrs, [t for t in succ if t in idx], barrier))
        keep = self.ALLF if self.keep_flags else frozenset()
        live_in = {b[0]: frozenset() for b in blocks}
        live_after = {}
        changed = True
        while changed:
            changed = False
            for start, addrs, succ, barrier in reversed(blocks):
                live = keep if barrier and not succ else frozenset()
                if not barrier:
                    for t in succ:
                        live = live | live_in[t]
                elif succ:   # call: nothing live across it
                    live = frozenset()
                if barrier and self.keep_flags and addrs and base_mnemonic(insns[addrs[-1]].mnemonic) in ('ret', 'retn'):
                    live = self.ALLF
                for a in reversed(addrs):
                    live_after[a] = live
                    uses, kills = self.flag_ud(insns[a])
                    live = (live - kills) | uses
                if live != live_in[start]:
                    live_in[start] = live
                    changed = True
        # ---- pass 2: emit ----
        body = []
        for start, addrs, succ, barrier in blocks:
            blk = (self.sync_out() + [f'(call $trace (i32.const {start}))']) if self.trace else []
            pc = start
            term = None
            for a in addrs:
                i = insns[a]
                self.live = live_after[a]
                try:
                    stm, t = self.insn(i, a + i.size)
                except (Unsupported, KeyError, IndexError, TypeError, ValueError) as ex:
                    self.unsupported[a] = i.mnemonic
                    stm, t = [f'(call $bad_icall (i32.const {a}))', '(unreachable)'], ('trap',)
                blk += self.prune(stm)
                pc = a + i.size
                if t is not None:
                    term = t + (pc,)
                    break
            if term is None:
                term = ('trap',) if not addrs or pc not in idx else ('fall', pc)
            blk += self.terminate(term, idx, tail_targets)
            body.append(blk)
        body, used = self.resolve_sync(body)
        structured = None
        if not self.region and os.environ.get('LIFT_STRUCTURED_LOOPS') != '0':
            structured = self.structured_loops(body, idx[self.entry])
        if self.region and os.environ.get('LIFT_REGION_BRANCHES') != '0':
            # The VM permits entry at every instruction, but static forward edges
            # can still skip the byte-offset dispatcher. Computed/backward targets
            # retain the original table so arbitrary resumes remain valid.
            target_blocks = {idx[l]: n for n, l in enumerate(leaders)}
            body = [self.direct_branches(n, blk, target_blocks) for n, blk in enumerate(body)]
        elif not self.region and structured is None:
            body = [self.direct_branches(n, blk) for n, blk in enumerate(body)]
        w = []
        w.append(f'(func ${self.name}' + (f' (export "{self.name}")' if self.export else ''))
        w.append('  ' + ' '.join(f'(local ${r} i32)' for r in FULL))
        w.append('  ' + ' '.join(f'(local ${f} i32)' for f in FLAGS))
        w.append('  (local $ta i32) (local $tb i32) (local $tr i32) (local $tc i32) (local $tx i32) (local $t64 i64) (local $pc i32)')
        w.append('  (local $tf f64) (local $tg f64) (local $ts f32) (local $tt f32) (local $tv v128) (local $ftop i32) (local $fpbase i32)')
        w.append('  ' + ' '.join(f'(local ${x} v128)' for x in XMMS))
        if any('$mm' in st for blk in body for st in blk):   # MMX registers (lift_mmx.py)
            w.append('  ' + ' '.join(f'(local $mm{k} v128)' for k in range(8)))
        for r in XMMS + FULL + FLAGS + FPU + ['fpbase']:
            if r in used:
                w.append(f'  (local.set ${r} (global.get $g_{r}))')
        if self.region:
            w.append(f'  (local.set $pc (i32.sub (global.get $g_vmpc) (i32.const {self.region[0]})))')
        else:
            w.append(f'  (local.set $pc (i32.const {idx[self.entry]}))')
        w.append('  (loop $dispatch')
        if structured is not None:
            w += structured
        else:
            w += self.dispatched_body(body, leaders, idx)
        w.append('  )')  # loop
        w.append('  (unreachable)')
        w.append(')')
        return '\n'.join(w)

    def dispatched_body(self, body, leaders, idx):
        nblocks = len(body)
        w = []
        for n in range(nblocks, 0, -1):
            w.append(f'    (block $b{n - 1}')
        if self.region:
            blk_of = {idx[l]: n for n, l in enumerate(leaders)}
            size = self.region[1] - self.region[0]
            w.append('      (br_table ' + ' '.join(f'$b{blk_of.get(c, 0)}' for c in range(size)) + ' $b0 (local.get $pc))')
        else:
            w.append('      (br_table ' + ' '.join(f'$b{n}' for n in range(nblocks)) + f' $b0 (local.get $pc))')
        for n in range(nblocks):
            w.append(f'    ) ;; block {n} @ {leaders[n]:#x}')
            w += ['      ' + s for s in body[n]]
            w.append('      (br $dispatch)')
        return w

    def structured_loops(self, body, entry):
        """Stackify single-entry contiguous cycles, retaining dispatch for irreducible graphs.

        Only functions with statically known local branches qualify. Interval loops cannot
        have an entry into their middle or cross another interval. No code is duplicated.
        The global br_table only needs the outer group headers; interior entries are
        unreachable because every local branch is emitted directly inside its scope.
        """
        texts = ['\n'.join(blk) for blk in body]
        if any('(br $dispatch)' in self.BRANCH.sub('', text) for text in texts):
            return None  # computed jumps and unwind resumes retain the original dispatcher
        edges = [(n, int(m.group(1))) for n, text in enumerate(texts) for m in self.BRANCH.finditer(text)]
        ends = {}
        for n, target in edges:
            if target <= n:
                ends[target] = max(ends.get(target, target), n)
        # Reject external entry into a loop interior, including the function's entry.
        ends = {h: t for h, t in ends.items() if not h < entry <= t and
                not any(h < b <= t and not h <= a <= t for a, b in edges)}
        crossing = set()
        for h, t in ends.items():
            for h2, t2 in ends.items():
                if h < h2 <= t < t2:
                    crossing.update((h, h2))
        ends = {h: t for h, t in ends.items() if h not in crossing}
        # A non-structured backedge may still use the global dispatcher, but it must
        # never target a block hidden inside a structured interval.
        changed = True
        while changed:
            invalid = {h for h, t in ends.items() for a, b in edges
                       if b <= a and h < b <= t and not (b in ends and a <= ends[b])}
            changed = bool(invalid)
            for h in invalid:
                del ends[h]
        if not ends:
            return None

        def branch(n, match):
            target = int(match.group(1))
            if target > n:
                return f'(br $b{target})'
            if target in ends and n <= ends[target]:
                return f'(br $l{target})'
            return match.group(0)

        def emit(lo, hi, container=None):
            groups, n = [], lo
            while n <= hi:
                end = ends[n] if n in ends and n != container else n
                groups.append((n, end, n in ends and n != container))
                n = end + 1
            w = []
            for n, _, _ in reversed(groups):
                if n != container:
                    w.append(f'    (block $b{n}')
            if container is None:
                labels = {}
                for n, end, _ in groups:
                    for k in range(n, end + 1):
                        labels[k] = n
                w.append('      (br_table ' + ' '.join(f'$b{labels[k]}' for k in range(len(body))) +
                         f' $b{labels[0]} (local.get $pc))')
            for n, end, loop in groups:
                if n != container:
                    w.append(f'    ) ;; block {n}')
                if loop:
                    w.append(f'    (loop $l{n}')
                    w += emit(n, end, n)
                    w.append('    )')
                else:
                    w.append(self.BRANCH.sub(lambda m: branch(n, m), texts[n]))
                    w.append('      (br $dispatch)')
            return w
        return emit(0, len(body) - 1)

    # Block n's code sits inside the labels of every later block, so a jump forward to block k is `br $bk`, straight
    # to its code; only backward jumps need the loop's br_table.
    BRANCH = re.compile(r'\(local\.set \$pc \(i32\.const (\d+)\)\)(\s*)\(br \$dispatch\)')

    def direct_branches(self, n, blk, target_blocks=None):
        text = '\n'.join(blk)
        if '$dispatch' not in text:
            return blk
        def replace(m):
            target = int(m.group(1))
            block = target if target_blocks is None else target_blocks.get(target, -1)
            return f'(br $b{block})' if block > n else m.group(0)
        text = self.BRANCH.sub(replace, text)
        return text.split('\n')

    def callf(self, tgt, tail=False):
        """Direct call when the callee lives in this chunk, else through the shared funcref table."""
        if self.chunk_of is not None and self.chunk_of.get(tgt, -1) != self.cur_chunk:
            return f'({"return_call_indirect" if tail else "call_indirect"} $tbl (type $ft) (i32.const {self.tidx(tgt)}))'
        return f'({"return_call" if tail else "call"} $f_{tgt:08x})'

    # CPU state lives in globals between functions and in locals inside one. Only the state a function touches is
    # copied: the sync points are emitted as markers and filled in by resolve_sync once the whole body is known.
    def sync_out(self):
        return [SYNC_OUT]

    def sync_in(self):
        return [SYNC_IN]

    def leaf_summary(self, tgt):
        # Region functions permit arbitrary instruction entry; keep their existing protocol.
        return LEAF_SUMMARIES.get(tgt) if ABI_CALLS and LEAF_CALLS and not self.region and not self.trace and tgt is not None and self.img.in_text(tgt) else None

    def sync_out_call(self, tgt=None):
        if self.leaf_summary(tgt) is not None:
            return [f'(;sync-leaf-out {tgt:x};)']
        return [SYNC_OUT_CALL] if ABI_CALLS else [SYNC_OUT]

    def sync_in_call(self, tgt=None):
        """State reload after a call that returned normally. Direct calls into this image skip the callee-saved
        registers the callee provably preserves; indirect, imported and cross-DLL callees are compiled functions
        that follow the ABI."""
        if not ABI_CALLS:
            return [SYNC_IN]
        if self.leaf_summary(tgt) is not None:
            return [f'(;sync-leaf-in {tgt:x};)']
        if tgt is not None and self.img.in_text(tgt):
            skip = PRESERVES.get(tgt, frozenset())
        else:
            skip = CALLEE_SAVED
        return [f'(;sync-in-call {",".join(r for r in CALLEE_SAVED if r in skip)};)']

    def resolve_sync(self, body):
        text = '\n'.join(s for blk in body for s in blk)
        used = set(re.findall(r'\(local\.(?:get|set|tee) \$(\w+)', text)) & STATE
        written = set(re.findall(r'\(local\.(?:set|tee) \$(\w+)', text)) & STATE
        # Stored: what the function changes. Reloaded after calls: everything it uses (callees may change any of it;
        # flags are not preserved across calls, as before).
        out = ' '.join(f'(global.set $g_{r} (local.get ${r}))' for r in FULL + FLAGS + XMMS + FPU if r in written)
        inn = ' '.join(f'(local.set ${r} (global.get $g_{r}))' for r in FULL + XMMS + FPU if r in used)
        out_call = ' '.join(f'(global.set $g_{r} (local.get ${r}))' for r in FULL + XMMS + FPU if r in written)
        def inn_call(m):
            skip = set(m.group(1).split(',')) if m.group(1) else set()
            return ' '.join(f'(local.set ${r} (global.get $g_{r}))' for r in FULL + XMMS + FPU if r in used and r not in skip)
        def leaf_sync(m):
            kind, address = m.groups()
            reads, writes = LEAF_SUMMARIES[int(address, 16)]
            if kind == 'in':
                return ' '.join(f'(local.set ${r} (global.get $g_{r}))' for r in FULL + XMMS + FPU if r in used and r in writes)
            # A leaf may overwrite its return address in guest memory. If it returns elsewhere,
            # publish the omitted, untouched caller state before the existing unwind handler.
            selected = reads if kind == 'out' else STATE - reads
            return ' '.join(f'(global.set $g_{r} (local.get ${r}))' for r in FULL + XMMS + FPU if r in written and r in selected)
        def fix(s):
            s = s.replace(SYNC_OUT_CALL, out_call).replace(SYNC_OUT, out).replace(SYNC_IN, inn)
            s = SYNC_IN_CALL.sub(inn_call, s) if '(;sync-in-call' in s else s
            return SYNC_LEAF.sub(leaf_sync, s) if '(;sync-leaf-' in s else s
        return [[x for x in (fix(s) for s in blk) if x] for blk in body], used

    def terminate(self, t, idx, tail):
        o = []
        kind = t[0]
        if kind == 'trap':
            return ['(unreachable)']
        if kind == 'fall':
            return [f'(local.set $pc (i32.const {idx[t[1]]}))', '(br $dispatch)']
        if kind == 'jmp':
            tgt = t[1]
            if tgt in idx:
                return [f'(local.set $pc (i32.const {idx[tgt]}))', '(br $dispatch)']
            return self.sync_out() + [self.callf(tgt, True)]
        if kind == 'jcc':
            _, c, tgt, nxt = t
            tgt_code = [f'(local.set $pc (i32.const {idx[tgt]}))'] if tgt in idx else None
            if tgt_code is None:
                raise Unsupported('jcc out of function')
            if nxt not in idx:
                return [f'(if {c} (then {tgt_code[0]} (br $dispatch)))', '(unreachable)']
            return [f'(if {c} (then {tgt_code[0]} (br $dispatch)))',
                    f'(local.set $pc (i32.const {idx[nxt]}))', '(br $dispatch)']
        if kind == 'jtab':
            _, ents, ix, _nxt = t
            n = len(ents)
            o.append(f'(local.set $ta {ix})')
            for k in range(n, -1, -1):
                o.append(f'(block $j{k}')
            o.append('(br_table ' + ' '.join(f'$j{k}' for k in range(n)) + f' $j{n} (local.get $ta))')
            for k in range(n):
                o.append(f') (local.set $pc (i32.const {idx[ents[k]]})) (br $dispatch)')
            o.append(') (unreachable)')
            return o
        if kind == 'ret':
            n = t[1]
            return (['(local.set $ta (i32.load (local.get $esp)))',
                     '(local.set $esp (i32.add (local.get $esp) (i32.const %d)))' % (4 + n),
                     '(global.set $g_ret (local.get $ta))'] + self.sync_out() + ['(return)'])
        if kind == 'ijmp':
            o.append(f'(local.set $ta {t[1]})')
            if self.region:
                lo, hi = self.region
                o.append(f'(if (i32.lt_u (i32.sub (local.get $ta) (i32.const {lo})) (i32.const {hi - lo})) '
                         f'(then (local.set $pc (i32.sub (local.get $ta) (i32.const {lo}))) (br $dispatch)))')
            o += self.sync_out()
            o += ['(global.set $g_target (local.get $ta))', '(return_call $dispatch)']
            return o
        if kind == 'call':
            _, tgt, indirect, nxt = t
            if indirect:
                # The effective address of CALL [esp+...] uses ESP before the
                # return address is pushed, as on x86 hardware.
                o.append(f'(local.set $ta {tgt})')
                o += self.push(f'(i32.const {nxt})')
                o += self.sync_out_call()
                o.append('(call $icall (local.get $ta))')
            else:
                o += self.push(f'(i32.const {nxt})')
                o += self.sync_out_call(tgt)
                o.append(self.callf(tgt))
            # A callee that unwound (longjmp / Lua error / coroutine) returns a different address than the one we pushed.
            if self.region:
                lo, hi = self.region
                resume = (f'(if (i32.lt_u (i32.sub (global.get $g_ret) (i32.const {lo})) (i32.const {hi - lo})) (then '
                          + ' '.join(self.sync_in()) + f' (local.set $pc (i32.sub (global.get $g_ret) (i32.const {lo}))) (br $dispatch)))')
            else:
                resume = ' '.join(
                    f'(if (i32.eq (global.get $g_ret) (i32.const {c})) (then ' + ' '.join(self.sync_in()) + f' (local.set $pc (i32.const {idx[c]})) (br $dispatch)))'
                    for c in self.setjmp_conts if c in idx and c != nxt)
            repair = f'(;sync-leaf-unwind {tgt:x};)' if not indirect and self.leaf_summary(tgt) is not None else ''
            o.append(f'(if (i32.ne (global.get $g_ret) (i32.const {nxt})) (then {repair} '
                     f'(call $unwind (i32.const {nxt}) (global.get $g_ret)) {resume} (return)))')
            o += self.sync_in_call(None if indirect else tgt)
            if nxt not in idx:
                return o + ['(unreachable)']      # noreturn call: nothing decodable follows
            o += [f'(local.set $pc (i32.const {idx[nxt]}))', '(br $dispatch)']
            return o
        raise Unsupported(kind)


def _full(name):
    return R16.get(name) or R8L.get(name) or R8H.get(name) or name


def leaf_register_summary(img, entry, entries=frozenset(), fs_base=0, shared_memory=False):
    """Conservative CPU-global footprint of a completely lifted call-free function.

    Derive the sets from emitted WAT, rather than trusting x86 ABI or Capstone's
    register metadata (partial-register operations and implicit operands matter).
    Every local used by a leaf is initialized from its CPU global, so reads include
    writes. This deliberate overestimate also makes abnormal-return repair safe.
    """
    f = Func(img, entry, entries=entries, fs_base=fs_base, shared_memory=shared_memory)
    try:
        insns, _ = f.discover_blocks()
        if not insns or len(insns) > 128:
            return None
        if any(base_mnemonic(i.mnemonic) in ('call', 'int', 'int3', 'ud2', 'hlt') for i in insns.values()):
            return None
        text = f.lift()
        if f.unsupported or re.search(r'\((?:call|call_indirect|return_call|return_call_indirect)\b', text):
            return None
        reads = frozenset(re.findall(r'\(global.get \$g_(\w+)\)', text)) & STATE
        writes = frozenset(re.findall(r'\(global.set \$g_(\w+)\b', text)) & STATE
        if not writes <= reads:
            return None
        return reads, writes
    except Exception:
        return None


def preserved_regs(img, entry, entries=frozenset()):
    """Callee-saved registers (ebx, esi, edi, ebp) that the function at `entry` leaves unchanged for its caller:
    never written, or pushed in the entry block and popped (`leave` for ebp) in every block that returns or jumps
    out. Anything else (MSVC's _EH_prolog / __SEH_prolog4 set up the caller's ebp; __SEH_epilog4 pops registers it
    never pushed) is treated as changed, so callers reload it."""
    f = Func(img, entry, entries=entries)
    try:
        insns, leaders = f.discover_blocks()
    except Exception:
        return frozenset()
    if entry not in insns:
        return frozenset()
    written = set()
    for i in insns.values():
        try:
            _, w = i.regs_access()
        except Exception:
            return frozenset()
        for r in w:
            n = _full(i.reg_name(r))
            if n in CALLEE_SAVED and not (i.mnemonic == 'push'):
                written.add(n)
    if not written:
        return frozenset(CALLEE_SAVED)
    starts = sorted(leaders)
    order = sorted(insns)
    # Entry block: instructions from the entry until the first leader after it.
    nxt_leader = min((l for l in starts if l > entry), default=None)
    entry_block = [insns[a] for a in order if a >= entry and (nxt_leader is None or a < nxt_leader)]
    pushed = {_full(i.op_str) for i in entry_block if i.mnemonic == 'push' and i.operands and i.operands[0].type == X86_OP_REG}
    exits = []
    for a in order:
        i = insns[a]
        op = i.operands[0] if i.operands else None
        if i.mnemonic in ('ret', 'retn') or (i.mnemonic == 'jmp' and op is not None and op.type == X86_OP_IMM and op.imm not in insns):
            exits.append(a)
        elif i.mnemonic == 'jmp' and op is not None and op.type != X86_OP_IMM:
            # A switch is `jmp [table + reg*4]` (stays in the function); any other indirect jump is a tail call.
            if not (op.type == X86_OP_MEM and op.mem.index != 0):
                exits.append(a)
    if not exits:
        return frozenset(r for r in CALLEE_SAVED if r not in written)
    keep = set(CALLEE_SAVED) - written
    for r in written:
        if r not in pushed:
            continue
        ok = True
        for a in exits:
            # The straight-line code leading to the exit (back to the previous ret/jmp), so pops in a fall-through
            # block before the one holding the ret count.
            k = order.index(a)
            block = [insns[a]]
            while k > 0 and order[k - 1] >= entry and insns[order[k - 1]].mnemonic not in ('ret', 'retn', 'jmp', 'int3'):
                k -= 1; block.append(insns[order[k]])
            if not any((i.mnemonic == 'pop' and _full(i.op_str) == r) or (r == 'ebp' and i.mnemonic == 'leave') for i in block):
                ok = False
                break
        if ok:
            keep.add(r)
    return frozenset(keep)


LD80 = r'''
 (func $ld80 (export "ld80") (param $a i32) (result f64) (local $m i64) (local $se i32) (local $e i32) (local $sc f64) (local $r f64)
  (local.set $m (i64.load (local.get $a))) (local.set $se (i32.load16_u offset=8 (local.get $a)))
  (local.set $e (i32.and (local.get $se) (i32.const 0x7fff)))
  (if (i32.eq (local.get $e) (i32.const 0x7fff))
    (then (local.set $r (select (f64.const inf) (f64.const nan) (i64.eqz (i64.and (local.get $m) (i64.const 0x7fffffffffffffff)))))
          (return (select (f64.neg (local.get $r)) (local.get $r) (i32.shr_u (local.get $se) (i32.const 15))))))
  (if (i64.eqz (local.get $m)) (then (return (select (f64.const -0) (f64.const 0) (i32.shr_u (local.get $se) (i32.const 15))))))
  (local.set $e (i32.sub (local.get $e) (i32.const 16446)))
  (if (i32.lt_s (local.get $e) (i32.const -2200)) (then (return (select (f64.const -0) (f64.const 0) (i32.shr_u (local.get $se) (i32.const 15))))))
  (if (i32.gt_s (local.get $e) (i32.const 2100)) (then (return (select (f64.const -inf) (f64.const inf) (i32.shr_u (local.get $se) (i32.const 15))))))
  (local.set $r (f64.convert_i64_u (local.get $m)))
  (local.set $r (f64.mul (local.get $r) (f64.reinterpret_i64 (i64.shl (i64.extend_i32_u (i32.add (i32.div_s (local.get $e) (i32.const 2)) (i32.const 1023))) (i64.const 52)))))
  (local.set $r (f64.mul (local.get $r) (f64.reinterpret_i64 (i64.shl (i64.extend_i32_u (i32.add (i32.sub (local.get $e) (i32.div_s (local.get $e) (i32.const 2))) (i32.const 1023))) (i64.const 52)))))
  (select (f64.neg (local.get $r)) (local.get $r) (i32.shr_u (local.get $se) (i32.const 15))))
 (func $st80 (export "st80") (param $a i32) (param $v f64) (local $b i64) (local $e i32) (local $s i32)
  (local.set $b (i64.reinterpret_f64 (local.get $v)))
  (local.set $s (i32.wrap_i64 (i64.shr_u (local.get $b) (i64.const 63))))
  (local.set $e (i32.wrap_i64 (i64.and (i64.shr_u (local.get $b) (i64.const 52)) (i64.const 0x7ff))))
  (if (i32.eqz (local.get $e)) (then (i64.store (local.get $a) (i64.const 0)) (i32.store16 offset=8 (local.get $a) (i32.shl (local.get $s) (i32.const 15))) (return)))
  (i64.store (local.get $a) (i64.or (i64.const 0x8000000000000000) (i64.shl (i64.and (local.get $b) (i64.const 0xfffffffffffff)) (i64.const 11))))
  (i32.store16 offset=8 (local.get $a) (i32.or (i32.shl (local.get $s) (i32.const 15))
     (select (i32.const 0x7fff) (i32.add (local.get $e) (i32.const 15360)) (i32.eq (local.get $e) (i32.const 0x7ff))))))
'''


def module(funcs_wat, mem_pages=64, extra='', import_mem=False, icall=None, table=None, elems='', import_cpu=False, cross_dll=False, shared_memory=False):
    ints = FULL + FLAGS + ['ret', 'target', 'vmpc', 'efl', 'df', 'ftop', 'fsw', 'fcw', 'fsbase', 'fpbase']
    cpu_imports = ''
    if import_cpu:
        cpu_imports = ''.join(f'(import "cpu" "{n}" (global $g_{n} (mut i32))) ' for n in ints)
        cpu_imports += ''.join(f'(import "cpu" "{n}" (global $g_{n} (mut v128))) ' for n in XMMS)
        g = ' '.join(f'(export "{n}" (global $g_{n}))' for n in ints + XMMS)
    else:
        defaults = {'fcw': '0x37f', 'fsbase': '0x1000', 'fpbase': '0x300'}
        g = ' '.join(f'(global $g_{n} (export "{n}") (mut i32) (i32.const {defaults.get(n, "0")}))' for n in ints)
        g += ' ' + ' '.join(f'(global $g_{n} (export "{n}") (mut v128) (v128.const i32x4 0 0 0 0))' for n in XMMS)
    imp = ''.join(f'(import "env" "{n}" (func $m_{n} (param f64) (result f64))) ' for n in ('sin', 'cos', 'tan', 'log2', 'exp2', 'exp2m1'))
    imp += '(import "env" "atan2" (func $m_atan2 (param f64 f64) (result f64))) (import "env" "fmod" (func $m_fmod (param f64 f64) (result f64)))'
    imp += '(import "env" "rdtsc" (func $rdtsc (result i64)))'
    imp += '(import "env" "unwind" (func $unwind (param i32 i32)))'
    if not import_mem:
        extra += ' (func $bad_icall (param i32) (unreachable))'
    if shared_memory and not import_mem:
        raise ValueError('shared memory must be imported')
    memdecl = ('(import "env" "mem" (memory 1 65536 shared))' if shared_memory else
               '(import "env" "mem" (memory 1))' if import_mem else f'(memory (export "mem") {mem_pages})')
    if import_mem:
        imp += '(import "env" "hostcall" (func $hostcall (param i32))) (import "env" "bad_icall" (func $bad_icall (param i32))) (import "env" "trace" (func $trace (param i32)))'
    if cross_dll:
        imp += '(import "env" "foreign_call" (func $foreign_call (param i32)))'
    tbl = f'(import "env" "tbl" (table $tbl {table} funcref)) (type $ft (func)) {elems}' if table is not None else ''
    return (f'(module\n {imp}\n {cpu_imports}\n {memdecl}\n {tbl}\n {g}\n'
            f' {LD80}\n {icall or "(func $icall (param i32) (unreachable)) (func $dispatch (unreachable))"}\n'
            f' {extra}\n' + '\n'.join(funcs_wat) + '\n)')

GLOBALS_I32 = FULL + FLAGS + ['ret', 'target', 'vmpc', 'efl', 'df', 'ftop', 'fsw', 'fcw', 'fsbase', 'fpbase']


def chunk_module(funcs_wat, elems, table, trace=True, shared_memory=False):
    """A size-bounded slice of a DLL: shares the core module's CPU globals and helpers by import."""
    imp = ''.join(f'(import "cpu" "{n}" (global $g_{n} (mut i32))) ' for n in GLOBALS_I32)
    imp += ''.join(f'(import "cpu" "{x}" (global $g_{x} (mut v128))) ' for x in XMMS)
    imp += ''.join(f'(import "env" "{n}" (func $m_{n} (param f64) (result f64))) ' for n in ('sin', 'cos', 'tan', 'log2', 'exp2', 'exp2m1'))
    imp += '(import "env" "atan2" (func $m_atan2 (param f64 f64) (result f64))) (import "env" "fmod" (func $m_fmod (param f64 f64) (result f64))) '
    imp += '(import "env" "rdtsc" (func $rdtsc (result i64))) (import "env" "unwind" (func $unwind (param i32 i32))) '
    imp += '(import "cpu" "ld80" (func $ld80 (param i32) (result f64))) (import "cpu" "st80" (func $st80 (param i32 f64))) '
    imp += '(import "cpu" "dispatch" (func $dispatch)) (import "cpu" "icall" (func $icall (param i32))) '
    imp += '(import "env" "bad_icall" (func $bad_icall (param i32))) (import "env" "trace" (func $trace (param i32))) '
    mem = '(memory 1 65536 shared)' if shared_memory else '(memory 1)'
    imp += f'(import "env" "mem" {mem}) (import "env" "tbl" (table $tbl {table} funcref)) (type $ft (func)) '
    return f'(module\n {imp}\n {elems}\n' + '\n'.join(funcs_wat) + '\n)'


def elem_segments(pairs):
    """[(table_index, func_name)] -> compact `(elem (i32.const i) $f ...)` runs."""
    pairs = sorted(pairs)
    out, run = [], []
    for i, n in pairs:
        if run and i == run[-1][0] + 1:
            run.append((i, n))
        else:
            if run:
                out.append(run)
            run = [(i, n)]
    if run:
        out.append(run)
    return ' '.join(f'(elem (i32.const {r[0][0]}) ' + ' '.join(f'${n}' for _, n in r) + ')' for r in out)


import lift_ext  # noqa: E402,F401  (installs Func.ext_insn)
