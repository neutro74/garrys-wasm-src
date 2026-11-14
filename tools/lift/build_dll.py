#!/usr/bin/env python3
"""Lift a PE DLL to chunked .wasm modules + image + metadata, with bounded memory.

  build_dll.py DLL --base 0x00400000 --out out/lua_shared [--vm-region lo,hi] [--chunk-insns 200000] [--opt]

Output: NAME.core.wasm (CPU globals, dispatcher, helpers, chunk 0) and NAME.cN.wasm (more functions).
All lifted functions live in one shared funcref table so calls cross chunk boundaries; each chunk is
assembled and freed as soon as it is full, so peak RAM is one chunk, not the whole DLL.
"""
import argparse, json, os, re, subprocess, sys, time
from census import Image, discover, sweep, tls_metadata
from lift import Func, Unsupported, module, chunk_module, elem_segments

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.environ.get('WASM_BIN', os.path.join(HERE, '../../third_party/binaryen'))
FEATURES = ['--enable-tail-call', '--enable-simd', '--enable-bulk-memory', '--enable-mutable-globals',
            '--enable-sign-ext', '--enable-nontrapping-float-to-int', '--enable-threads']
SPARE_THUNKS = 1024


def assemble(wat_path, wasm_path, opt, debug):
    cmd = [os.path.join(BIN, 'wasm-as'), *(['-g'] if debug else []), *FEATURES, '-o', wasm_path, wat_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'wasm-as failed for {wat_path}:\n{r.stderr[:2000]}')
    if opt:
        r = subprocess.run([os.path.join(BIN, 'wasm-opt'), *FEATURES, f'-{opt}', '-o', wasm_path, wasm_path], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f'wasm-opt failed for {wasm_path}:\n{r.stderr[:2000]}')
    os.remove(wat_path)


PAGE_BITS = 12


def paged(addrs, callf):
    """Address -> lifted function: a br_table over 4 KiB pages, then a binary search within the page (a few
    compares instead of ~15 over the whole image)."""
    def gen(xs, lo, hi):
        if hi - lo == 1:
            return f'(if (i32.eq (local.get $a) (i32.const {xs[lo]})) (then {callf(xs[lo])}))'
        mid = (lo + hi) // 2
        return f'(if (i32.lt_u (local.get $a) (i32.const {xs[mid]})) (then {gen(xs, lo, mid)}) (else {gen(xs, mid, hi)}))'
    base = addrs[0] & ~((1 << PAGE_BITS) - 1)
    pages = {}
    for x in addrs:
        pages.setdefault((x - base) >> PAGE_BITS, []).append(x)
    npages = max(pages) + 1
    if npages > 1 << 16:                    # not one image: plain search
        return gen(addrs, 0, len(addrs))
    order = sorted(pages)
    label = {p: k for k, p in enumerate(order)}
    o = [f'(local.set $p (i32.shr_u (i32.sub (local.get $a) (i32.const {base})) (i32.const {PAGE_BITS})))',
         f'(if (i32.lt_u (local.get $p) (i32.const {npages})) (then (block $miss']
    o += [f'(block $pg{k}' for k in range(len(order) - 1, -1, -1)]
    o.append('(br_table ' + ' '.join(f'$pg{label[p]}' if p in label else '$miss' for p in range(npages)) + ' $miss (local.get $p))')
    for k, p in enumerate(order):
        o.append(f') {gen(pages[p], 0, len(pages[p]))} (br $miss)')
    o.append(')))')
    return '\n  '.join(o)


def dispatch_wat(addrs, callf, thunk_base, n_thunks, region, cross_dll=False, setjmp_points=()):
    """Core `$dispatch` ()->(): reads $g_target, tail-calls the lifted function / VM region / host thunk."""
    vm = ''
    if region:
        lo, hi = region
        vm = (f'(if (i32.lt_u (i32.sub (local.get $a) (i32.const {lo})) (i32.const {hi - lo})) '
              f'(then (global.set $g_vmpc (local.get $a)) {callf("vm")}))')
    fallback = '$foreign_call' if cross_dll else '$bad_icall'
    # longjmp ends in a jump to the continuation after setjmp: unwind to the frame that owns it.
    sj = ''.join(f'(if (i32.eq (local.get $a) (i32.const {c})) (then (global.set $g_ret (local.get $a)) (return)))\n  ' for c in sorted(setjmp_points))
    return (f'(func $dispatch (export "dispatch")\n  (local $a i32) (local $p i32) (local.set $a (global.get $g_target))\n'
            f'  (if (i32.lt_u (i32.sub (local.get $a) (i32.const {thunk_base})) (i32.const {n_thunks * 4}))\n'
            f'    (then (call $hostcall (i32.shr_u (i32.sub (local.get $a) (i32.const {thunk_base})) (i32.const 2))) (return)))\n'
            f'  {sj}{vm}\n  {paged(addrs, callf) if addrs else ""}\n  (call {fallback} (local.get $a)))\n'
            f'(func $icall (export "icall") (param $a i32) (global.set $g_target (local.get $a)) (call $dispatch))')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dll'); ap.add_argument('--base', type=lambda x: int(x, 0), required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--thunk-base', type=lambda x: int(x, 0))
    ap.add_argument('--vm-region', help='lo,hi rva of a computed-entry asm region (LuaJIT VM)')
    ap.add_argument('--chunk-insns', type=int, default=200000, help='x86 instructions per chunk (bounds peak RAM)')
    # O1 cuts ~22%% at ~25x the chunk size in memory; Oz saves a little more but needs ~250x (9.7 GB on a 38 MB chunk).
    ap.add_argument('--opt', nargs='?', const='O1', default=None, choices=['O1', 'O2', 'Os', 'Oz'], help='wasm-opt level for each chunk (default O1 when given)')
    ap.add_argument('--debug', action='store_true', help='keep function names (wasm-as -g)')
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--cpuid-clear-edx', type=lambda x: int(x, 0), default=0, help='CPUID.1:EDX feature bits this module must not see (e.g. 0x4000000 hides SSE2)')
    ap.add_argument('--cross-dll', action='store_true', help='route unknown guest calls to another loaded DLL')
    ap.add_argument('--import-cpu', action='store_true', help='share CPU globals with the first loaded DLL')
    ap.add_argument('--shared-memory', action='store_true', help='import a shared memory for thread-capable hosts')
    ap.add_argument('--fs-base', type=lambda x: int(x, 0), default=0x1000)
    ap.add_argument('--abi-calls', action='store_true', help='skip flag stores, preserved reloads and proven-leaf sync (LIFT_LEAF_CALLS=0 disables leaf summaries)')
    a = ap.parse_args()
    import lift_ext; lift_ext.CPUID_CLEAR_EDX = a.cpuid_clear_edx
    t0 = time.time()
    os.makedirs(os.path.dirname(a.out) or '.', exist_ok=True)

    img = Image(a.dll)
    pref = img.base
    img.rebase(a.base)
    imports = []
    for ent in img.pe.DIRECTORY_ENTRY_IMPORT:
        for imp in ent.imports:
            imports.append({'dll': ent.dll.decode().lower(), 'name': imp.name.decode() if imp.name else f'#{imp.ordinal}',
                            'iat': imp.address - pref + a.base})
    thunk_base = a.thunk_base or (a.base + ((img.size + 0xffff) & ~0xffff) + 0x10000)

    seeds, _ = discover(img)
    region = None
    if a.vm_region:
        rlo, rhi = (int(x, 0) for x in a.vm_region.split(','))
        region = (a.base + rlo, a.base + rhi)
        probe = Func(img, region[0], region=region)
        rinsns, _ = probe.discover_region()
        for i in rinsns.values():
            if i.mnemonic in ('call', 'jmp') and i.operands and i.operands[0].type == 2:
                t = i.operands[0].imm
                if img.in_text(t) and not (region[0] <= t < region[1]):
                    seeds.add(t)
        del rinsns, probe
    funcs, _ = sweep(img, seeds)
    if region:
        funcs = {k: v for k, v in funcs.items() if not (region[0] <= k < region[1])}
    order = sorted(funcs)
    sizes = {e: len(funcs[e]) for e in order}
    del funcs

    # chunk assignment by instruction count; chunk 0 is the core module (also holds dispatcher + stubs)
    chunk_of, tbl = {}, {}
    cid, acc = 0, 0
    for e in order:
        chunk_of[e] = cid
        acc += sizes[e]
        if acc >= a.chunk_insns:
            cid, acc = cid + 1, 0
    if region:
        chunk_of['vm'] = 0

    def tidx(addr):
        return tbl.setdefault(addr, len(tbl))
    for e in order:
        tidx(e)
    if region:
        tidx('vm')
    entries = frozenset(order)
    if a.abi_calls:
        import lift as lift_abi
        lift_abi.ABI_CALLS = True
        t1 = time.time()
        lift_abi.PRESERVES.update({e: lift_abi.preserved_regs(img, e, entries) for e in order if e != 'vm' and not (region and region[0] <= e < region[1])})
        print(f'abi-calls: preserve sets for {len(lift_abi.PRESERVES)} functions in {time.time() - t1:.0f}s', flush=True)
        lift_abi.LEAF_SUMMARIES.clear()
        for e in order:
            if not lift_abi.LEAF_CALLS or a.trace or sizes[e] > 128 or (region and region[0] <= e < region[1]):
                continue
            summary = lift_abi.leaf_register_summary(img, e, entries, fs_base=a.fs_base, shared_memory=a.shared_memory)
            if summary is not None:
                lift_abi.LEAF_SUMMARIES[e] = summary
        print(f'abi-calls: safe leaf summaries for {len(lift_abi.LEAF_SUMMARIES)} functions', flush=True)
    lifted, failed, unsup = set(), {}, {}
    refs = set()
    pending = {}                       # chunk id -> [(table index, name)] for the chunk being built
    bufs = {}                          # chunk id -> [wat]
    sizes_chars = {}
    chunk_files = []
    nchunks_flushed = 0

    def name_of(e):
        return 'f_vm_region' if e == 'vm' else f'f_{e:08x}'

    def new_func(e, ents):
        return Func(img, e, fs_base=a.fs_base, entries=ents, trace=a.trace, chunk_of=chunk_of, cur_chunk=chunk_of[e], tidx=tidx, shared_memory=a.shared_memory)

    def flush(c, final=False):
        if c == 0:
            return
        parts = bufs.pop(c, [])
        if not parts:
            return
        pend = pending.pop(c, [])
        defined = {n for _, n in pend}
        direct = set(re.findall(r'(?:call|return_call) \$(f_[0-9a-f]{8})', '\n'.join(parts)))
        for n in sorted(direct - defined):      # direct callee in this chunk that failed to lift
            parts.append(f'(func ${n} (call $bad_icall (i32.const {int(n[2:], 16)})) (unreachable))')
        wat = chunk_module(parts, elem_segments(pend), table=0, shared_memory=a.shared_memory)
        path = f'{a.out}.c{c}'
        # table size is patched in once known: write a placeholder token
        open(path + '.wat', 'w').write(wat)
        chunk_files.append((c, path))

    work = list(order)
    seen = set(work)
    done_in_chunk = {}
    while work:
        e = work.pop(0)
        c = chunk_of[e]
        try:
            fn_ = new_func(e, entries)
            text = fn_.lift()
            unsup.update(fn_.unsupported)
        except Exception as ex:
            first = f'{type(ex).__name__}: {ex}'
            f2 = new_func(e, entries)
            try:
                f2.discover_blocks()
            except Exception:
                pass
            tails = {i.operands[0].imm for i in f2.seen_insns.values()
                     if i.mnemonic == 'jmp' and i.operands and i.operands[0].type == 2 and i.operands[0].imm != e}
            text = None
            if tails:
                try:
                    fn_ = new_func(e, entries | frozenset(tails))
                    text = fn_.lift()
                    unsup.update(fn_.unsupported)
                    for t in tails:
                        if t not in seen and img.in_text(t):
                            seen.add(t); work.append(t); entries = entries | {t}
                            chunk_of[t] = chunk_of[e]; tidx(t)
                except Exception:
                    text = None
            if text is None:
                failed[e] = first
                continue
        lifted.add(e)
        refs.update(int(m, 16) for m in re.findall(r'\$f_([0-9a-f]{8})', text))
        if c == 0:
            bufs.setdefault(0, []).append(text)
            pending.setdefault(0, []).append((tidx(e), name_of(e)))
        else:
            bufs.setdefault(c, []).append(text)
            pending.setdefault(c, []).append((tidx(e), name_of(e)))
        sizes_chars[c] = sizes_chars.get(c, 0) + len(text)
        # flush a non-core chunk when its last function has been lifted
        remaining = any(chunk_of.get(w) == c for w in work)
        if c != 0 and not remaining:
            flush(c)
    for c in list(bufs):
        if c != 0:
            flush(c, True)

    # core module: VM region, stubs, dispatcher, helpers, chunk 0 functions
    core = bufs.pop(0, [])
    core_pending = pending.pop(0, [])
    if region:
        vm = Func(img, region[0], fname='f_vm_region', fs_base=a.fs_base, region=region,
                  trace=a.trace, chunk_of=chunk_of, cur_chunk=0, tidx=tidx, shared_memory=a.shared_memory)
        core.append(vm.lift())
        unsup.update(vm.unsupported)
        core_pending.append((tidx('vm'), 'f_vm_region'))
    in_region = (lambda x: region and region[0] <= x < region[1])
    stubs = sorted(x for x in (set(k for k in tbl if k != 'vm') | set(refs) | set(failed)) if x not in lifted)
    for s_ in stubs:
        if in_region(s_):
            core.append(f'(func $f_{s_:08x} (global.set $g_vmpc (i32.const {s_})) (return_call_indirect $tbl (type $ft) (i32.const {tidx("vm")})))')
        else:
            core.append(f'(func $f_{s_:08x} (call $bad_icall (i32.const {s_})) (unreachable))')
        core_pending.append((tidx(s_), f'f_{s_:08x}'))
    # dispatcher

    def callf(x):
        idx = tidx(x)
        return f'(return_call_indirect $tbl (type $ft) (i32.const {idx}))'
    addrs = sorted(x for x in (lifted | set(stubs)) if not in_region(x))
    import lift as lift_mod
    disp = dispatch_wat(addrs, callf, thunk_base, len(imports) + SPARE_THUNKS, region, a.cross_dll, lift_mod.SETJMP_CONT)
    table_size = len(tbl)
    core_wat = module(core, import_mem=True, icall=disp, table=table_size, elems=elem_segments(core_pending),
                      import_cpu=a.import_cpu, cross_dll=a.cross_dll, shared_memory=a.shared_memory)
    with open(a.out + '.core.wat', 'w') as fh:
        fh.write(core_wat)
    del core_wat, core
    assemble(a.out + '.core.wat', a.out + '.core.wasm', a.opt, a.debug)

    chunk_names = []
    for c, path in sorted(chunk_files):
        w = open(path + '.wat').read().replace('(table $tbl 0 funcref)', f'(table $tbl {table_size} funcref)')
        open(path + '.wat', 'w').write(w)
        del w
        assemble(path + '.wat', path + '.wasm', a.opt, a.debug)
        chunk_names.append(os.path.basename(path + '.wasm'))

    open(a.out + '.img', 'wb').write(bytes(img.mem))
    exports, forwarders = {}, {}
    for sym in getattr(img.pe.DIRECTORY_ENTRY_EXPORT, 'symbols', []):
        keys = [f'#{sym.ordinal}'] + ([sym.name.decode()] if sym.name else [])
        for key in keys:
            if sym.forwarder:
                forwarders[key] = sym.forwarder.decode()
            else:
                exports[key] = a.base + sym.address
    json.dump({'base': a.base, 'size': img.size, 'thunk_base': thunk_base, 'imports': imports, 'spare_thunks': SPARE_THUNKS,
               'module_name': os.path.basename(a.dll).lower(), 'import_cpu': a.import_cpu, 'cross_dll': a.cross_dll,
               'shared_memory': a.shared_memory,
               'exports': exports, 'forwarders': forwarders, 'tls': tls_metadata(img),
               'entry': a.base + img.pe.OPTIONAL_HEADER.AddressOfEntryPoint,
               'core': os.path.basename(a.out) + '.core.wasm', 'chunks': chunk_names, 'table_size': table_size,
               'dispatch': [x for e in addrs if e not in lift_mod.SETJMP_CONT for x in (e, tbl[e])],   # host/fast-dispatch.mjs
               'failed': {hex(k): v for k, v in failed.items()}, 'lifted': len(lifted),
               'unsupported_insns': {hex(k): v for k, v in unsup.items()}}, open(a.out + '.json', 'w'), indent=1)
    total = os.path.getsize(a.out + '.core.wasm') + sum(os.path.getsize(os.path.join(os.path.dirname(a.out) or '.', n)) for n in chunk_names)
    print(f'{os.path.basename(a.dll)}: {len(lifted)} lifted, {len(failed)} failed, {len(stubs)} stubs, '
          f'{1 + len(chunk_names)} modules, wasm {total / 1e6:.1f} MB in {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
