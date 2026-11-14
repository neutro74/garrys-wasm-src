#!/usr/bin/env python3
"""Build two synthetic x86 DLL-shaped modules and execute nested cross-DLL calls."""
import json
import os
import subprocess
import tempfile
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from build_dll import BIN, FEATURES, dispatch_wat
from lift import Func, module


class Image:
    def __init__(self, base):
        self.base = base
        self.mem = bytearray(0x4000)
        self.ranges = []

    def add(self, offset, asm):
        addr = self.base + offset
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(asm, addr=addr)[0])
        self.mem[offset:offset + len(code)] = code
        self.ranges.append((addr, addr + len(code)))
        return addr

    def in_text(self, addr):
        return any(lo <= addr < hi for lo, hi in self.ranges)


def build(directory, name, base, functions, imports, import_cpu, tls=False, shared=False, spare_thunks=64):
    img = Image(base)
    exports = {n: img.add(offset, asm) for n, offset, asm in functions}
    funcs = [Func(img, addr, fs_base=0x1000, shared_memory=shared).lift() for addr in exports.values()]
    if tls:
        img.mem[0x2000:0x2008] = bytes.fromhex('44 33 22 11 21 43 65 87')
    disp = dispatch_wat(sorted(exports.values()), lambda a: f'(return_call $f_{a:08x})',
                        base + 0x10000, len(imports) + spare_thunks, None, True)
    wat = module(funcs, import_mem=True, icall=disp, import_cpu=import_cpu, cross_dll=True, shared_memory=shared)
    prefix = os.path.join(directory, name)
    with open(prefix + '.wat', 'w') as f:
        f.write(wat)
    subprocess.run([os.path.join(BIN, 'wasm-as'), *FEATURES, '-o', prefix + '.core.wasm', prefix + '.wat'], check=True)
    with open(prefix + '.img', 'wb') as f:
        f.write(img.mem)
    meta = dict(base=base, size=len(img.mem), module_name=name + '.dll', import_cpu=import_cpu,
                cross_dll=True, core=name + '.core.wasm', chunks=[], table_size=0,
                shared_memory=shared,
                imports=imports, thunk_base=base + 0x10000, spare_thunks=spare_thunks,
                exports=exports, failed={}, unsupported_insns={})
    if tls:
        meta['tls'] = dict(start=base + 0x2000, size=8, zero_fill=4, index=base + 0x2010,
                           callbacks=[exports['tls_callback']])
        meta['entry'] = exports['dllmain']
    elif 'dllmain' in exports:
        meta['entry'] = exports['dllmain']
    with open(prefix + '.json', 'w') as f:
        json.dump(meta, f)


class MultiDLLTests(unittest.TestCase):
    def test_actual_guest_thread(self):
        with tempfile.TemporaryDirectory(prefix='lift-guest-thread-', dir='/tmp/opencode') as d:
            build(d, 'primary', 0x400000,
                  [('dllmain', 0x1000, 'mov eax, [esp+8]; cmp eax, 2; jne not_attach; lock inc dword ptr [0x402808]; not_attach: cmp eax, 3; jne done; lock inc dword ptr [0x40280c]; done: mov eax, 1; ret 0xc'),
                   ('worker', 0x1100, 'push 0x402820; call dword ptr [0x403000]; mov eax, [esp+4]; lock add dword ptr [0x402800], eax; push 0x402820; call dword ptr [0x403004]; push -1; push dword ptr [0x402850]; call dword ptr [0x403008]; mov eax, fs:[0x18]; mov [0x402804], eax; mov eax, 0x42; ret 4')],
                  [dict(dll='kernel32.dll', name='EnterCriticalSection', iat=0x403000),
                   dict(dll='kernel32.dll', name='LeaveCriticalSection', iat=0x403004),
                   dict(dll='kernel32.dll', name='WaitForSingleObject', iat=0x403008)], False, shared=True)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-guest-thread.mjs'), d], check=True)

    def test_nested_import_calls(self):
        with tempfile.TemporaryDirectory(prefix='lift-multidll-', dir='/tmp/opencode') as d:
            build(d, 'primary', 0x400000,
                  [('entry', 0x1000, 'mov eax, 9; call dword ptr [0x403000]; add eax, 5; ret'),
                   ('back', 0x1100, 'add eax, 100; ret'),
                   ('tls_callback', 0x1200, 'mov edx, fs:[0x2c]; mov ecx, [0x402010]; mov edx, [edx+ecx*4]; inc dword ptr [edx]; ret 0xc'),
                   ('dllmain', 0x1300, 'mov eax, fs:[0x2c]; mov ecx, [0x402010]; mov eax, [eax+ecx*4]; cmp dword ptr [eax], 0x11223345; sete al; movzx eax, al; ret 0xc')],
                  [dict(dll='secondary.dll', name='work', iat=0x403000)], False, tls=True)
            build(d, 'secondary', 0x600000,
                  [('work', 0x1000, 'add eax, 7; call dword ptr [0x603000]; ret')],
                  [dict(dll='primary.dll', name='back', iat=0x603000)], True)
            subprocess.run(['node', os.path.join(os.path.dirname(__file__), 'host/test-multi.mjs'), d], check=True)


if __name__ == '__main__':
    unittest.main()
