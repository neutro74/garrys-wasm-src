#!/usr/bin/env python3
"""Compare shared-memory atomic instruction results to Unicorn x86."""
import json
import os
import struct
import subprocess
import tempfile
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import UC_X86_REG_EFLAGS

from build_dll import BIN, FEATURES
from difftest import CODE, DATA, STACK, FakeImg, FLAGBIT, UREG
from lift import Func, module


class AtomicTests(unittest.TestCase):
    def test_atomic_inc_under_worker_contention(self):
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm('lock inc dword ptr [ebx]; ret', addr=CODE)[0])
        wat = module([Func(FakeImg(code), CODE, export=True, shared_memory=True).lift()], import_mem=True, shared_memory=True)
        with tempfile.TemporaryDirectory(prefix='lift-atomic-contention-', dir='/tmp/opencode') as d:
            src, dst = os.path.join(d, 'atomic.wat'), os.path.join(d, 'atomic.wasm')
            with open(src, 'w') as out: out.write(wat)
            subprocess.run([os.path.join(BIN, 'wasm-as'), *FEATURES, '-o', dst, src], check=True)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-atomic-contention.mjs'), dst], check=True)

    def test_atomics_against_unicorn(self):
        cases = [
            ('lock add dword ptr [ebx], eax', 0x7fffffff, 1, 2),
            ('lock sub dword ptr [ebx], eax', 0, 1, 2),
            ('lock inc dword ptr [ebx]', 0x7fffffff, 1, 2),
            ('lock dec dword ptr [ebx]', 0x80000000, 1, 2),
            ('lock and dword ptr [ebx], eax', 0xf0f05555, 0xff0ffff0, 2),
            ('lock or dword ptr [ebx], eax', 0xf0f05555, 0xff0ffff0, 2),
            ('lock xor dword ptr [ebx], eax', 0xf0f05555, 0xff0ffff0, 2),
            ('lock xadd dword ptr [ebx], eax', 0xffffffff, 1, 2),
            ('lock cmpxchg dword ptr [ebx], ecx', 7, 7, 99),
            ('lock cmpxchg dword ptr [ebx], ecx', 7, 8, 99),
            ('xchg dword ptr [ebx], eax', 7, 8, 99),
            ('lock add byte ptr [ebx], al', 0x123456ff, 1, 2),
            ('lock xadd word ptr [ebx], ax', 0x1234ffff, 1, 2),
            ('lock cmpxchg word ptr [ebx], cx', 0x12340007, 7, 99),
            ('lock cmpxchg word ptr [ebx], cx', 0x12340007, 8, 99),
            ('lock cmpxchg8b qword ptr [ebx]', 7, 7, 99),
            ('lock cmpxchg8b qword ptr [ebx]', 7, 8, 99),
        ]
        regs0 = dict(eax=0, ebx=DATA, ecx=0, edx=0x87654321, esi=0, edi=0, ebp=0, esp=STACK + 0x800)
        flags = dict(cf=1, pf=1, zf=0, sf=1, of=1)
        inputs, expected = [], []
        ks = Ks(KS_ARCH_X86, KS_MODE_32)
        with tempfile.TemporaryDirectory(prefix='lift-atomic-', dir='/tmp/opencode') as d:
            for n, (asm, word, eax, ecx) in enumerate(cases):
                regs = dict(regs0, eax=eax, ecx=ecx)
                code = bytes(ks.asm(asm + '; ret', addr=CODE)[0])
                f = Func(FakeImg(code), CODE, export=True, keep_flags=True, shared_memory=True)
                wat = module([f.lift()], import_mem=True, shared_memory=True)
                src, dst = os.path.join(d, f'{n}.wat'), os.path.join(d, f'{n}.wasm')
                with open(src, 'w') as out:
                    out.write(wat)
                subprocess.run([os.path.join(BIN, 'wasm-as'), *FEATURES, '-o', dst, src], check=True)
                word2 = 0x87654321
                inputs.append(dict(wasm=dst, regs=regs, flags=flags, word=word, word2=word2))
                uc = Uc(UC_ARCH_X86, UC_MODE_32)
                uc.mem_map(CODE, 0x1000); uc.mem_write(CODE, code)
                uc.mem_map(DATA, 0x1000); uc.mem_write(DATA, struct.pack('<II', word, word2))
                uc.mem_map(STACK, 0x1000); uc.mem_write(STACK + 0x800, struct.pack('<I', 0xdead0000))
                for r, v in regs.items(): uc.reg_write(UREG[r], v)
                uc.reg_write(UC_X86_REG_EFLAGS, 2 | sum(flags[k] << b for k, b in FLAGBIT.items()))
                uc.emu_start(CODE, 0xdead0000, count=16)
                ef = uc.reg_read(UC_X86_REG_EFLAGS)
                a, b = struct.unpack('<II', uc.mem_read(DATA, 8))
                expected.append(dict(regs={r: uc.reg_read(UREG[r]) for r in regs},
                                     flags={r: ef >> bit & 1 for r, bit in FLAGBIT.items()}, word=a, word2=b))
            config = os.path.join(d, 'inputs.json')
            with open(config, 'w') as out: json.dump(inputs, out)
            output = subprocess.check_output(['node', os.path.join(os.path.dirname(__file__), 'host/test-atomic-run.mjs'), config], text=True)
            for (asm, *_), got, want in zip(cases, json.loads(output), expected):
                with self.subTest(asm=asm): self.assertEqual(got, want)


if __name__ == '__main__':
    unittest.main()
