import math
import struct
import unittest
import wasmtime
from capstone import Cs,CS_ARCH_X86,CS_MODE_32
from difftest import CODE,STACK,FakeImg,ks
from lift import Func,module

class RegionBranches(unittest.TestCase):
    def test_static_fallthrough_uses_direct_labels(self):
        code=bytes(ks.asm('mov eax, 5; add eax, 7; ret',addr=CODE)[0])
        wat=Func(FakeImg(code),CODE,region=(CODE,CODE+len(code)),export=True).lift()
        self.assertIn('(br $b1)',wat)

    def test_arbitrary_entries_and_backward_branch(self):
        code=bytes(ks.asm('mov ecx, 4; again: add eax, 3; dec ecx; jnz again; ret; mov eax, 9; ret',addr=CODE)[0])
        wat=module([Func(FakeImg(code),CODE,region=(CODE,CODE+len(code)),export=True).lift()])
        store=wasmtime.Store()
        def host(fn,n):return wasmtime.Func(store,wasmtime.FuncType([wasmtime.ValType.f64()]*n,[wasmtime.ValType.f64()]),fn)
        imports=[host(math.sin,1),host(math.cos,1),host(math.tan,1),host(math.log2,1),host(lambda x:2**x,1),host(lambda x:2**x-1,1),host(math.atan2,2),host(math.fmod,2),wasmtime.Func(store,wasmtime.FuncType([],[wasmtime.ValType.i64()]),lambda:0),wasmtime.Func(store,wasmtime.FuncType([wasmtime.ValType.i32()]*2,[]),lambda a,b:None)]
        ex=wasmtime.Instance(store,wasmtime.Module(store.engine,wat),imports).exports(store)
        ins=list(Cs(CS_ARCH_X86,CS_MODE_32).disasm(code,CODE))
        for index,want in [(0,13),(1,10),(5,9)]:
            ex['eax'].set_value(store,1);ex['ecx'].set_value(store,3)
            ex['esp'].set_value(store,STACK);ex['mem'].write(store,struct.pack('<I',0xdead0000),STACK)
            ex['vmpc'].set_value(store,ins[index].address)
            ex[f'f_{CODE:08x}'](store)
            self.assertEqual(ex['eax'].value(store),want)

if __name__ == '__main__':unittest.main()
