"""Register synchronization across proven call-free direct callees."""
import math
import struct
import unittest
from unittest.mock import patch

import wasmtime
from capstone import Cs, CS_ARCH_X86, CS_MODE_32
from difftest import CODE, STACK, FakeImg, ks
import lift


class LeafSyncTests(unittest.TestCase):
    def program(self, caller, callee):
        code = bytes(ks.asm(caller + '; ret; target: ' + callee, addr=CODE)[0])
        insns_detail = Cs(CS_ARCH_X86, CS_MODE_32); insns_detail.detail = True
        call = next(i for i in insns_detail.disasm(code, CODE) if i.mnemonic == 'call')
        return FakeImg(code), call.operands[0].imm

    def build(self, img, target, optimized):
        entries = frozenset((CODE, target))
        summary = lift.leaf_register_summary(img, target, entries)
        self.assertIsNotNone(summary)
        with patch.object(lift, 'ABI_CALLS', True), patch.object(lift, 'LEAF_SUMMARIES', {target: summary} if optimized else {}):
            caller = lift.Func(img, CODE, export=True, entries=entries).lift()
            callee = lift.Func(img, target, entries=entries).lift()
        store = wasmtime.Store()
        def host(fn, n):
            return wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.f64()] * n, [wasmtime.ValType.f64()]), fn)
        imports = [host(math.sin, 1), host(math.cos, 1), host(math.tan, 1), host(math.log2, 1),
                   host(lambda x: 2 ** x, 1), host(lambda x: 2 ** x - 1, 1), host(math.atan2, 2), host(math.fmod, 2),
                   wasmtime.Func(store, wasmtime.FuncType([], [wasmtime.ValType.i64()]), lambda: 0),
                   wasmtime.Func(store, wasmtime.FuncType([wasmtime.ValType.i32()] * 2, []), lambda a, b: None)]
        ex = wasmtime.Instance(store, wasmtime.Module(store.engine, lift.module([caller, callee])), imports).exports(store)
        for i, reg in enumerate(lift.FULL): ex[reg].set_value(store, i + 100)
        ex['esp'].set_value(store, STACK)
        ex['mem'].write(store, struct.pack('<III', 0xdead0000, 77, 88), STACK)
        ex[f'f_{CODE:08x}'](store)
        return caller, {reg: ex[reg].value(store) & 0xffffffff for reg in lift.FULL + ['ret']}

    def compare(self, caller, callee):
        img, target = self.program(caller, callee)
        baseline, want = self.build(img, target, False)
        optimized, got = self.build(img, target, True)
        self.assertEqual(got, want)
        return baseline, optimized, got

    def test_stale_globals_are_neither_stored_nor_reloaded(self):
        before, after, got = self.compare('mov edx, 0x12345678; mov ebx, 55; call target; add eax, edx', 'mov eax, 9; ret')
        self.assertEqual(got['eax'], 0x12345681)
        self.assertLess(after.split('(call $f_')[0].count('(global.set $g_edx'),
                        before.split('(call $f_')[0].count('(global.set $g_edx'))
        self.assertLess(after.count('(global.get $g_edx'), before.count('(global.get $g_edx'))

    def test_partial_register_write_reads_previous_full_register(self):
        _, _, got = self.compare('mov eax, 0x12345678; call target', 'mov al, 0xab; ret')
        self.assertEqual(got['eax'], 0x123456ab)

    def test_both_leaf_branch_paths_and_register_changes(self):
        for value in [0, 1]:
            _, _, got = self.compare(f'mov ecx, {value}; mov edx, 123; call target; add eax, edx',
                                    'test ecx, ecx; jz zero; mov edx, 3; mov eax, 9; ret; zero: mov eax, 4; ret')
            self.assertEqual(got['eax'], 127 if value == 0 else 12)

    def test_ret_immediate_and_stack_arguments(self):
        _, _, got = self.compare('push 6; call target', 'mov eax, dword ptr [esp+4]; ret 4')
        self.assertEqual((got['eax'], got['esp']), (6, STACK + 4))

    def test_unexpected_return_repairs_omitted_globals_before_propagating(self):
        _, _, got = self.compare('mov edx, 0x12345678; call target; mov edx, 2',
                                'mov dword ptr [esp], 0xdeadbeef; ret')
        self.assertEqual((got['edx'], got['ret']), (0x12345678, 0xdeadbeef))

    def test_reject_nonleaf_tailcall_host_helpers_and_unknown_instructions(self):
        for source in ['call target; ret; target: ret', 'jmp eax', 'rdtsc; ret', 'ud2', 'fcos; ret']:
            code = bytes(ks.asm(source, addr=CODE)[0])
            self.assertIsNone(lift.leaf_register_summary(FakeImg(code), CODE))

    def test_unknown_indirect_region_and_trace_fallback_remain_identical(self):
        img, target = self.program('mov edx, 7; call target; add eax, edx', 'mov eax, 9; ret')
        summary = lift.leaf_register_summary(img, target, (CODE, target))
        for options in [{'trace': True}, {'region': (CODE, target)}]:
            with patch.object(lift, 'ABI_CALLS', True), patch.object(lift, 'LEAF_SUMMARIES', {}):
                before = lift.Func(img, CODE, entries=(CODE, target), **options).lift()
            with patch.object(lift, 'ABI_CALLS', True), patch.object(lift, 'LEAF_SUMMARIES', {target: summary}):
                after = lift.Func(img, CODE, entries=(CODE, target), **options).lift()
            self.assertEqual(before, after)
        for source in ['mov eax, 0; call eax; ret', f'call {CODE + 1000}; ret']:
            other = FakeImg(bytes(ks.asm(source, addr=CODE)[0]))
            with patch.object(lift, 'ABI_CALLS', True), patch.object(lift, 'LEAF_SUMMARIES', {}):
                before = lift.Func(other, CODE).lift()
            with patch.object(lift, 'ABI_CALLS', True), patch.object(lift, 'LEAF_SUMMARIES', {target: summary}):
                after = lift.Func(other, CODE).lift()
            self.assertEqual(before, after)


if __name__ == '__main__': unittest.main()
