#!/usr/bin/env python3
"""Differential tester: lifted WASM vs Unicorn on random machine states, per instruction sequence."""
import random, struct, sys
import wasmtime
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_MEM_UNMAPPED
from unicorn.x86_const import *
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from keystone import KsError
from lift import Func, module, FULL, FLAGS, Unsupported

CODE, DATA, DATA_SZ, STACK = 0x1000, 0x10000, 0x1000, 0x20000
UREG = dict(eax=UC_X86_REG_EAX, ecx=UC_X86_REG_ECX, edx=UC_X86_REG_EDX, ebx=UC_X86_REG_EBX,
            esp=UC_X86_REG_ESP, ebp=UC_X86_REG_EBP, esi=UC_X86_REG_ESI, edi=UC_X86_REG_EDI)
FLAGBIT = dict(cf=0, pf=2, zf=6, sf=7, of=11)
ks = Ks(KS_ARCH_X86, KS_MODE_32)

class FakeImg:
    base = 0
    def __init__(self, code): self.mem = bytearray(0x1000) + bytearray(code)
    def in_text(self, va): return CODE <= va < CODE + len(self.mem) - 0x1000

def interesting(rng):
    return rng.choice([0, 1, 2, 0x7f, 0x80, 0xff, 0x100, 0x7fff, 0x8000, 0xffff, 0x7fffffff, 0x80000000,
                       0xffffffff, 0xfffffffe, rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(8)])

def run_case(asm, n=200, seed=1, mem_regs=('ebx',)):
    asm, *dirs = [x.strip() for x in asm.split(' @')]
    ign = set(); mode = None; prune = False
    for dname in dirs:
        if dname.startswith('ignore='): ign = set(dname[7:].split(','))
        elif dname in ('sse', 'f32', 'f64', 'fpu', 'f64sse', 'mmx'): mode = dname
        elif dname == 'prune': prune = True; ign = set(FLAGS)
    if mode in ('sse', 'f32', 'f64sse'):
        asm = ('movups xmm0, [ebx]; movups xmm1, [ebx+0x10]; movups xmm2, [ebx+0x20]; ' + asm +
               '; movups [ebx+0x80], xmm0; movups [ebx+0x90], xmm1; movups [ebx+0xa0], xmm2')
    elif mode == 'mmx':
        asm = ('movq mm0, [ebx]; movq mm1, [ebx+8]; movq mm2, [ebx+0x10]; ' + asm +
               '; movq [ebx+0x80], mm0; movq [ebx+0x88], mm1; movq [ebx+0x90], mm2; emms')
    elif mode == 'fpu' or mode == 'f64':
        asm = 'fldcw word ptr [ebx+0x60]; ' + asm
    fixed_ebx = mode is not None
    code = bytes(ks.asm(asm + '; ret', addr=CODE)[0])
    img = FakeImg(code)
    img.mem = bytearray(CODE) + bytearray(code)
    img.base = 0
    f = Func(img, CODE, keep_flags=not prune, export=True)
    wat = module([f.lift()])
    eng = wasmtime.Engine(); store = wasmtime.Store(eng)
    import math
    def hf(fn, n): return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
    imps = [hf(math.sin, 1), hf(math.cos, 1), hf(math.tan, 1), hf(lambda x: math.log2(x) if x > 0 else float('-inf'), 1),
            hf(lambda x: 2.0 ** x, 1), hf(lambda x: 2.0 ** x - 1, 1), hf(math.atan2, 2),
            hf(lambda a, b: math.fmod(a, b) if b else float('nan'), 2)]
    imps.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0))
    imps.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
    inst = wasmtime.Instance(store, wasmtime.Module(eng, wat), imps)
    ex = inst.exports(store)
    mem, fn = ex['mem'], ex[f.name]
    rng = random.Random(seed)
    completed = 0
    for t in range(n):
        regs = {r: interesting(rng) for r in FULL}
        regs['esp'] = STACK + 0x800
        for r in mem_regs: regs[r] = DATA + 0x400 + (0 if fixed_ebx else rng.randrange(0, 0x100))
        flags = {k: rng.getrandbits(1) for k in FLAGS}
        if mode == 'f64sse':
            fl = [rng.uniform(-100, 100) for _ in range(DATA_SZ // 8)]
            data = struct.pack(f'<{DATA_SZ // 8}d', *fl)
            data = data[:0x460] + struct.pack('<HH', 0x27f, 0) + data[0x464:]
        elif mode in ('sse', 'f32'):
            fl = [rng.choice([rng.uniform(-1000, 1000), rng.uniform(-1, 1), 0.0, -0.0, 1.0, 3.5, rng.uniform(-3e5, 3e5), float(rng.randrange(-40, 40))]) for _ in range(DATA_SZ // 4)]
            data = struct.pack(f'<{DATA_SZ // 4}f', *fl)
            data = data[:0x460] + struct.pack('<I', 0) + data[0x464:]
        elif mode in ('fpu', 'f64'):
            fl = [rng.choice([rng.uniform(-1000, 1000), rng.uniform(-1, 1), 0.0, 1.0, -2.5, rng.uniform(-3e9, 3e9), float(rng.randrange(-40, 40)), rng.uniform(0.01, 100)]) for _ in range(DATA_SZ // 8)]
            data = bytearray(struct.pack(f'<{DATA_SZ // 8}d', *fl))
            data = bytes(data)
        else:
            data = bytes(rng.getrandbits(8) for _ in range(DATA_SZ))
        if mode in ('fpu', 'f64'):
            data = data[:0x460] + struct.pack('<HHI', 0x27f, 0, 0) + data[0x468:]
        if mode in ('sse', 'f32'):
            data = data[:0x460] + struct.pack('<HH', 0x27f, 0) + data[0x464:]
        stack = bytes(rng.getrandbits(8) for _ in range(0x1000))
        # unicorn
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(CODE, 0x1000); uc.mem_write(CODE, code)
        uc.mem_map(DATA, 0x2000); uc.mem_write(DATA, data)
        uc.mem_map(STACK, 0x1000); uc.mem_write(STACK, stack)
        for r, v in regs.items(): uc.reg_write(UREG[r], v)
        ef = 2
        for k, b in FLAGBIT.items(): ef |= flags[k] << b
        uc.reg_write(UC_X86_REG_EFLAGS, ef)
        uc.mem_write(regs['esp'], struct.pack('<I', 0xdead0000))
        try:
            uc.emu_start(CODE, 0xdead0000, count=2048)
        except Exception as e:
            continue  # faulting case (e.g. div by 0): skip
        if uc.reg_read(UC_X86_REG_EIP) != 0xdead0000:
            continue  # instruction limit reached, not a completed execution
        # wasm
        for r in FULL: ex[r].set_value(store, struct.unpack('<i', struct.pack('<I', regs[r]))[0])
        for k in FLAGS: ex[k].set_value(store, flags[k])
        ex['ftop'].set_value(store, 0); ex['fsw'].set_value(store, 0); ex['fcw'].set_value(store, 0x37f)
        mem.write(store, bytes(8 * 8), 0x300)
        mem.write(store, data, DATA); mem.write(store, stack, STACK)
        mem.write(store, struct.pack('<I', 0xdead0000), regs['esp'])
        try:
            fn(store)
        except wasmtime.Trap as e:
            if 'divide' in str(e) or 'overflow' in str(e): continue
            if 'bad_icall' in str(e): raise Unsupported('runtime-unsupported instruction')
            raise
        bad = []
        for r in FULL:
            got = ex[r].value(store) & 0xffffffff
            want = uc.reg_read(UREG[r])
            if r == 'esp': want = want  # ret popped
            if got != want: bad.append(f'{r}: got {got:#x} want {want:#x}')
        uef = uc.reg_read(UC_X86_REG_EFLAGS)
        for k, b in FLAGBIT.items():
            if k in ign: continue
            got = ex[k].value(store); want = (uef >> b) & 1
            if got != want: bad.append(f'{k}: got {got} want {want}')
        if mode in ('fpu', 'f64') and ex['ftop'].value(store) != (uc.reg_read(UC_X86_REG_FPSW) >> 11 & 7): bad.append('ftop differs')
        if bytes(mem.read(store, DATA, DATA + DATA_SZ)) != bytes(uc.mem_read(DATA, DATA_SZ)): bad.append('data memory differs')
        if bytes(mem.read(store, STACK, STACK + 0x1000)) != bytes(uc.mem_read(STACK, 0x1000)): bad.append('stack memory differs')
        if bad:
            return False, f'{asm!r} case {t} regs={ {k: hex(v) for k, v in regs.items()} } flags={flags}: ' + '; '.join(bad)
        completed += 1
    if not completed:
        return False, f'{asm!r}: no execution completed successfully'
    return True, ''

if __name__ == '__main__':
    CASES = (open(__file__.replace('difftest.py', 'cases.txt')).read().split('\n') + open(__file__.replace('difftest.py', 'cases_prune.txt')).read().split('\n')) if len(sys.argv) < 2 else (open(sys.argv[1][1:]).read().split('\n') if sys.argv[1].startswith('@') else sys.argv[1:])
    ok = fail = unsup = badasm = 0
    for line in CASES:
        line = line.strip()
        if not line or line.startswith('#'): continue
        try:
            good, msg = run_case(line)
        except KsError as e:
            badasm += 1; print(f'BADASM {line}'); continue
        except Unsupported as e:
            unsup += 1; print(f'UNSUP {line}  ({e})'); continue
        if good: ok += 1
        else: fail += 1; print('FAIL', msg[:500])
    print(f'{ok} pass, {fail} fail, {unsup} unsupported, {badasm} invalid assembly')
    sys.exit(1 if fail or unsup or badasm else 0)
