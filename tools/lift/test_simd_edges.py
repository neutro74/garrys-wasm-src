#!/usr/bin/env python3
"""Check SSE reciprocal edge cases against the x86 specification, not Unicorn.

Unicorn does not model the unconditional denormal flushing of RCPPS/RSQRTPS.
"""
import math
import struct
import unittest

from keystone import Ks, KS_ARCH_X86, KS_MODE_32
import wasmtime

from difftest import CODE, DATA, STACK, FakeImg
from lift import Func, module


def execute(mnemonic, words):
    asm = f'movups xmm1, [ebx]; {mnemonic} xmm0, xmm1; movups [ebx+16], xmm0; ret'
    code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(asm, addr=CODE)[0])
    wat = module([Func(FakeImg(code), CODE, export=True).lift()])
    store = wasmtime.Store()
    def host(fn, n):
        return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
    imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
               host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2)]
    imports.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0))
    imports.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
    ex = wasmtime.Instance(store, wasmtime.Module(store.engine, wat), imports).exports(store)
    ex['ebx'].set_value(store, DATA)
    ex['esp'].set_value(store, STACK)
    ex['mem'].write(store, struct.pack('<4I', *words), DATA)
    ex['mem'].write(store, struct.pack('<I', 0xdead0000), STACK)
    ex[f'f_{CODE:08x}'](store)
    return struct.unpack('<4I', ex['mem'].read(store, DATA + 16, DATA + 32))


class ReciprocalEdges(unittest.TestCase):
    def test_rcpps_flushes_input_and_output(self):
        self.assertEqual(execute('rcpps', [1, 0x80000001, 0x7f7fffff, 0xff7fffff]),
                         (0x7f800000, 0xff800000, 0, 0x80000000))

    def test_rsqrtps_flushes_inputs_and_preserves_zero_sign(self):
        self.assertEqual(execute('rsqrtps', [1, 0x80000001, 0, 0x80000000]),
                         (0x7f800000, 0xff800000, 0x7f800000, 0xff800000))

    def test_normal_values_and_infinities(self):
        self.assertEqual(execute('rcpps', [0x3f800000, 0xbf800000, 0x7f800000, 0xff800000]),
                         (0x3f800000, 0xbf800000, 0, 0x80000000))
        self.assertEqual(execute('rsqrtps', [0x3f800000, 0x40800000, 0x7f800000, 0x80000000]),
                         (0x3f800000, 0x3f000000, 0, 0xff800000))


if __name__ == '__main__':
    unittest.main()
