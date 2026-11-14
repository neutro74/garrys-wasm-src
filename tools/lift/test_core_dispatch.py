import unittest
import wasmtime
from core_dispatch import transform

WAT='''(module
 (type $0 (func))
 (import "r" "slow" (func $slow (param i32)))
 (memory (export "memory") 1)
 (table 4 funcref)
 (global $target (mut i32) (i32.const 0))
 (export "target" (global $target))
 (global $hit (export "hit") (mut i32) (i32.const 0))
 (func $dispatch (type $0) (call $slow (global.get $target)))
 (func $callee (global.set $hit (i32.add (global.get $hit) (i32.const 1))))
 (func $internal (call $dispatch))
 (elem (i32.const 1) $callee)
 (export "dispatch" (func $dispatch))
 (export "internal" (func $internal)))'''

class CoreDispatchTests(unittest.TestCase):
 def test_lookup_internal_calls_collision_zero_and_original_fallback(self):
  engine=wasmtime.Engine();store=wasmtime.Store(engine);miss=[]
  ex=wasmtime.Instance(store,wasmtime.Module(engine,transform(WAT)),[wasmtime.Func(store,wasmtime.FuncType([wasmtime.ValType.i32()],[]),lambda a:miss.append(a&0xffffffff))]).exports(store)
  ex['target'].set_value(store,0x1234);ex['dispatch'](store);self.assertEqual(miss,[0x1234])
  ex['core_dispatch_base'].set_value(store,256);ex['core_dispatch_shift'].set_value(store,28);ex['core_dispatch_mask'].set_value(store,15)
  import struct
  # Deliberately colliding keys exercise linear probing. Function slot is stable.
  addresses=[a for a in range(1,1000) if ((a*0x9e3779b1)&0xffffffff)>>28==3][:3]
  for i,a in enumerate(addresses):ex['memory'].write(store,struct.pack('<II',a,1),256+(3+i)*8)
  for a in addresses:
   ex['target'].set_value(store,a);ex['internal'](store)
  self.assertEqual(ex['hit'].value(store),3)
  high=0xf1234567;h=((high*0x9e3779b1)&0xffffffff)>>28
  while h in [3,4,5]:h=(h+1)&15
  ex['memory'].write(store,struct.pack('<II',high,1),256+h*8)
  # Use the original hashed slot, with probing through any populated keys.
  ex['target'].set_value(store,high-2**32);ex['dispatch'](store)
  self.assertEqual(ex['hit'].value(store),4)
  for a in [0,0x1234,0xffffffff]:
   ex['target'].set_value(store,a if a<2**31 else a-2**32);ex['internal'](store)
  self.assertEqual(miss,[0x1234,0,0x1234,0xffffffff])
  ex['memory'].grow(store,1);ex['target'].set_value(store,addresses[0]);ex['dispatch'](store);self.assertEqual(ex['hit'].value(store),5)
 def test_reject_double_patch(self):
  with self.assertRaises(ValueError):transform(transform(WAT))

if __name__=='__main__':unittest.main()
