"""Adds `dispatch` (flat [address, table index, ...]) to the metadata of already-built DLLs.

The host uses it for a hash-table dispatcher (tools/lift/host/fast-dispatch.mjs) in place of the core module's
generated one. build_dll.py writes the same map for new builds; this recovers it from a core module's dispatcher
code: each leaf is `local.get $a; i32.const ADDR; i32.eq; if; i32.const INDEX; return_call_indirect`. Leaves of the
form `... if; local.get $a; global.set $g_ret` are longjmp continuations and stay with the original dispatcher.

    python dispatch_map.py out/ [out-next/]
"""
import glob
import json
import os
import re
import sys


def sleb(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7f) << s
        s += 7
        if x < 0x80:
            if x & 0x40:
                r -= 1 << s
            return r, i


def extract(core):
    b = open(core, 'rb').read()
    pairs, conts = {}, set()
    for m in re.finditer(rb'\x41', b):
        try:
            a, k = sleb(b, m.start() + 1)
            if b[k:k + 3] != b'\x46\x04\x40':
                continue
            if b[k + 3] == 0x41:
                idx, k2 = sleb(b, k + 4)
                if b[k2] == 0x13:
                    pairs[a & 0xffffffff] = idx
            elif b[k + 3:k + 5] == b'\x20\x00' and b[k + 5] == 0x24:
                conts.add(a & 0xffffffff)
        except IndexError:
            pass
    for c in conts:
        pairs.pop(c, None)
    return pairs


def main():
    for d in sys.argv[1:]:
        for j in sorted(glob.glob(os.path.join(d, '*.json'))):
            try:
                meta = json.load(open(j))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(meta, dict) or 'core' not in meta or 'table_size' not in meta:
                continue
            core = os.path.join(os.path.dirname(j), meta['core'])
            if not os.path.exists(core):
                continue
            pairs = extract(core)
            if any(i >= meta['table_size'] for i in pairs.values()):
                print('skip (index out of range)', j)
                continue
            meta['dispatch'] = [x for a in sorted(pairs) for x in (a, pairs[a])]
            json.dump(meta, open(j, 'w'))
            print(f'{os.path.basename(j)}: {len(pairs)} of {meta["table_size"]}')


if __name__ == '__main__':
    main()
