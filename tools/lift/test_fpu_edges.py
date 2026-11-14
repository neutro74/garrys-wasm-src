#!/usr/bin/env python3
"""Architectural edge cases not accurately modelled by Unicorn's x87."""
import math
import struct
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_32
import wasmtime

from difftest import CODE, DATA, STACK, FakeImg
from lift import Func, module


class FpuEdges(unittest.TestCase):
    def test_fsincos_out_of_range_leaves_value_and_stack_unchanged(self):
        asm = 'fld qword ptr [ebx]; fsincos; fnstsw ax; fstp qword ptr [ebx+8]; ret'
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(asm, addr=CODE)[0])
        store = wasmtime.Store()
        def host(fn, n):
            return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
        imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
                   host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2)]
        imports.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0))
        imports.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
        ex = wasmtime.Instance(store, wasmtime.Module(store.engine, module([Func(FakeImg(code), CODE, export=True).lift()])), imports).exports(store)
        ex['ebx'].set_value(store, DATA)
        ex['esp'].set_value(store, STACK)
        ex['mem'].write(store, struct.pack('<d', 2.0 ** 63), DATA)
        ex['mem'].write(store, struct.pack('<I', 0xdead0000), STACK)
        ex[f'f_{CODE:08x}'](store)
        self.assertEqual(ex['eax'].value(store) & 0x7c00, 0x3c00)  # TOP=7, C2=1
        self.assertEqual(ex['ftop'].value(store), 0)
        self.assertEqual(struct.unpack('<d', ex['mem'].read(store, DATA + 8, DATA + 16))[0], 2.0 ** 63)


if __name__ == '__main__':
    unittest.main()
