"""Add a bounded hash lookup before a core's original address dispatcher.

Works on Binaryen WAT in a staging directory; original fallback is retained for
thunks, foreign calls, VM region entries and setjmp continuations. Table slots
and all guest machine-state instructions are left intact.
"""
import re


def transform(wat):
    if 'core_dispatch_base' in wat:
        raise ValueError('already transformed')
    dispatch = re.search(r'\(export "dispatch" \(func (\$[^\s)]+)\)\)', wat).group(1)
    target = re.search(r'\(export "target" \(global (\$[^\s)]+)\)\)', wat).group(1)
    start = re.search(r'\(func '+re.escape(dispatch)+r'\s', wat).start()
    depth=0
    for end in range(start,len(wat)):
        if wat[end]=='(': depth+=1
        elif wat[end]==')':
            depth-=1
            if depth==0: break
    original=wat[start:end+1]
    typ=re.search(r'\(type (\$[^\s)]+)\)',original).group(1)
    assert re.search(r'\(type '+re.escape(typ)+r' \(func\)\)',wat), 'dispatch must be () -> ()'
    slow=original.replace('(func '+dispatch+' ', '(func $core_dispatch_slow ',1)
    new=f'''(func {dispatch} (type {typ})
      (local $a i32) (local $h i32) (local $k i32)
      (if (i32.eqz (global.get $core_dispatch_base)) (then (return_call $core_dispatch_slow)))
      (local.set $a (global.get {target}))
      (if (i32.eqz (local.get $a)) (then (return_call $core_dispatch_slow)))
      (local.set $h (i32.shr_u (i32.mul (local.get $a) (i32.const -1640531535)) (global.get $core_dispatch_shift)))
      (block $miss (loop $probe
        (local.set $k (i32.load (i32.add (global.get $core_dispatch_base) (i32.shl (local.get $h) (i32.const 3)))))
        (if (i32.eq (local.get $k) (local.get $a))
          (then (return_call_indirect (type {typ}) (i32.load offset=4 (i32.add (global.get $core_dispatch_base) (i32.shl (local.get $h) (i32.const 3)))))))
        (br_if $miss (i32.eqz (local.get $k)))
        (local.set $h (i32.and (i32.add (local.get $h) (i32.const 1)) (global.get $core_dispatch_mask)))
        (br $probe)))
      (return_call $core_dispatch_slow))'''
    extra='\n'+slow+'\n'+''.join(f' (global $core_dispatch_{name} (export "core_dispatch_{name}") (mut i32) (i32.const 0))\n' for name in ['base','shift','mask'])
    return (wat[:start]+new+wat[end+1:]).rstrip()[:-1]+extra+')\n'
