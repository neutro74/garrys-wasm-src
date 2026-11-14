#!/usr/bin/env python3
"""Execution regression tests for call semantics."""
import math
import struct
import unittest

from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_ESP
import wasmtime

from difftest import CODE, STACK, FakeImg
from lift import Func, module


class CallTests(unittest.TestCase):
    def test_rdtsc_splits_host_counter_without_changing_flags(self):
        code = bytes.fromhex('0f 31 c3')
        store = wasmtime.Store()
        def host(fn, n):
            return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
        imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
                   host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2)]
        imports.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0x123456789abcdef0))
        imports.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
        ex = wasmtime.Instance(store, wasmtime.Module(store.engine, module([Func(FakeImg(code), CODE, export=True, keep_flags=True).lift()])), imports).exports(store)
        ex['esp'].set_value(store, STACK)
        ex['cf'].set_value(store, 1)
        ex['zf'].set_value(store, 1)
        ex['mem'].write(store, struct.pack('<I', 0xdead0000), STACK)
        ex[f'f_{CODE:08x}'](store)
        self.assertEqual(ex['eax'].value(store) & 0xffffffff, 0x9abcdef0)
        self.assertEqual(ex['edx'].value(store) & 0xffffffff, 0x12345678)
        self.assertEqual((ex['cf'].value(store), ex['zf'].value(store)), (1, 1))

    def test_indirect_stack_operand_is_read_before_push(self):
        code = bytes(Ks(KS_ARCH_X86, KS_MODE_32).asm(
            'call dword ptr [esp+4]; ret; mov eax, 0x1234; ret', addr=CODE)[0])
        insns = list(Cs(CS_ARCH_X86, CS_MODE_32).disasm(code, CODE))
        callee = insns[2].address
        img = FakeImg(code)
        funcs = [Func(img, CODE, export=True).lift(), Func(img, callee).lift()]
        dispatch = (f'(func $icall (param $a i32) '
                    f'(if (i32.ne (local.get $a) (i32.const {callee})) (then (unreachable))) '
                    f'(call $f_{callee:08x})) (func $dispatch (unreachable))')
        store = wasmtime.Store()
        def host(fn, n):
            return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
        imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
                   host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2)]
        imports.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0))
        imports.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
        ex = wasmtime.Instance(store, wasmtime.Module(store.engine, module(funcs, icall=dispatch)), imports).exports(store)
        stack = struct.pack('<II', 0xdead0000, callee)
        ex['esp'].set_value(store, STACK + 0x800)
        ex['mem'].write(store, stack, STACK + 0x800)
        ex[f'f_{CODE:08x}'](store)

        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(CODE, 0x1000)
        uc.mem_write(CODE, code)
        uc.mem_map(STACK, 0x1000)
        uc.mem_write(STACK + 0x800, stack)
        uc.reg_write(UC_X86_REG_ESP, STACK + 0x800)
        uc.emu_start(CODE, 0xdead0000, count=16)
        self.assertEqual(ex['eax'].value(store), uc.reg_read(UC_X86_REG_EAX))
        self.assertEqual(ex['esp'].value(store), uc.reg_read(UC_X86_REG_ESP))

    def test_address_size_override_loop_and_xlat(self):
        # The loop uses CX, preserving the upper half of ECX. XLAT wraps the
        # BX+AL address to 16 bits before reading it.
        code = bytes.fromhex('b9 03 00 01 00 40 67 e2 fc b8 08 00 00 00 bb fa ff 01 00 67 d7 c3')
        store = wasmtime.Store()
        def host(fn, n):
            return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
        imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
                   host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2)]
        imports.append(wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0))
        imports.append(wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), lambda _expected, _actual: None))
        ex = wasmtime.Instance(store, wasmtime.Module(store.engine, module([Func(FakeImg(code), CODE, export=True).lift()])), imports).exports(store)
        stack = struct.pack('<I', 0xdead0000)
        ex['esp'].set_value(store, STACK + 0x800)
        ex['mem'].write(store, stack, STACK + 0x800)
        ex['mem'].write(store, b'\x7c', 2)
        ex[f'f_{CODE:08x}'](store)

        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0, 0x2000)
        uc.mem_write(CODE, code)
        uc.mem_write(2, b'\x7c')
        uc.mem_map(STACK, 0x1000)
        uc.mem_write(STACK + 0x800, stack)
        uc.reg_write(UC_X86_REG_ESP, STACK + 0x800)
        uc.emu_start(CODE, 0xdead0000, count=32)
        for name, reg in [('eax', UC_X86_REG_EAX), ('ebx', UC_X86_REG_EBX),
                          ('ecx', UC_X86_REG_ECX), ('esp', UC_X86_REG_ESP)]:
            self.assertEqual(ex[name].value(store) & 0xffffffff, uc.reg_read(reg), name)


if __name__ == '__main__':
    unittest.main()
