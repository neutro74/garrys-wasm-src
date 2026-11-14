#!/usr/bin/env python3
"""Lift every discovered function of a PE DLL; report coverage and output size."""
import sys, time, collections
from census import Image, discover, sweep
from lift import Func, Unsupported, module

def main(path, limit=None, out=None):
    img = Image(path)
    seeds, _ = discover(img)
    funcs, _ = sweep(img, seeds)
    fs, bad, why = [], 0, collections.Counter()
    t0 = time.time()
    entries = sorted(funcs)[:limit]
    for e in entries:
        try:
            fs.append(Func(img, e).lift())
        except Unsupported as ex:
            bad += 1; why[str(ex)] += 1
        except Exception as ex:
            bad += 1; why[f'{type(ex).__name__}: {ex}'] += 1
    import re
    wat = module(fs)
    defined = set(re.findall(r'\(func \$(f_[0-9a-f]{8})', wat))
    refs = set(re.findall(r'\$(f_[0-9a-f]{8})', wat)) - defined
    wat = wat[:-1] + ''.join(f'(func ${r} (unreachable))\n' for r in sorted(refs)) + ')'
    print(f'{path}: {len(entries)} functions, {len(fs)} lifted, {bad} failed, WAT {len(wat)/1e6:.1f} MB, {time.time()-t0:.0f}s')
    for k, v in why.most_common(25): print(f'   {v:>5}  {k}')
    if out:
        import wasmtime
        t1 = time.time()
        wasm = wasmtime.wat2wasm(wat)
        open(out, 'wb').write(wasm)
        print(f'assembled {len(wasm)/1e6:.1f} MB wasm in {time.time()-t1:.0f}s -> {out}')
    return wat

if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != '-' else None, sys.argv[3] if len(sys.argv) > 3 else None)
