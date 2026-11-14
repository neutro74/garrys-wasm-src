# tools/lift — x86 PE → WebAssembly static recompiler

Lifts 32-bit Windows DLLs (built for the real Garry's Mod install) to WebAssembly so the **actual**
retail game code runs in GameBox. Python for analysis and lifting; Node only for the runtime host.

```
python3 -m venv .venv && .venv/bin/pip install pefile capstone unicorn wasmtime keystone-engine lief
.venv/bin/python inspect_pe.py DLL...          # sections, imports, protection hints, Source interface versions
.venv/bin/python census.py DLL                 # instruction census / function discovery
.venv/bin/python build_dll.py DLL --base 0x00400000 --out out/NAME [--vm-region lo,hi] [--trace]
node host/lua-tests.mjs out/lua_shared         # 17 Lua checks on the lifted real lua_shared.dll
.venv/bin/python difftest.py                   # ~330 differential cases: lifted WASM vs Unicorn
.venv/bin/python -m unittest -v test_calls test_simd_edges test_fpu_edges test_multi_dll \
    test_atomic test_shared_memory test_files test_retail
node host/filesystem-init.mjs "$HOME/.steam/steam/steamapps/common/GarrysMod"
node host/server-tests.mjs
```

## Design

* Guest address space == wasm linear memory. The image is relocated at lift time (`--base`), so code
  immediates are final absolute addresses.
* One wasm function per guest function; registers/flags in wasm locals, synced to exported globals
  around calls. Eager flags with dead-flag pruning (liveness over blocks); intra-instruction flag
  dependencies go through `$tx` (see below).
* Control flow inside a function: `loop` + `br_table` block dispatcher (works for irreducible CFGs).
  `switch` jump tables are resolved (`jump_table`). Indirect calls/jumps go through `$dispatch`
  (binary search over lifted addresses; thunk range → host imports); indirect `jmp` is a real
  `return_call`.
* **Returns honour the guest return address** (`$g_ret`). After every call the caller checks the
  callee returned to *its* continuation; otherwise it resumes (asm VM region) or propagates. This is
  what makes LuaJIT errors, coroutines and longjmp-style unwinding work.
* **Asm VM regions** (LuaJIT's interpreter) with computed entry points: `--vm-region` lifts the whole
  range as one dispatcher where every instruction address is an entry.
* x87: register stack in guest memory (`FPU_BASE`), TOP/FSW/FCW in wasm globals, f64 precision.
  SSE/SSE2: xmm0-7 as `v128` locals. DF/EFLAGS-misc/MXCSR/CPUID/XGETBV are modelled (CPUID reports
  SSE2 only, no AVX).
* Unsupported instructions lift to a trap that names the address (`meta.unsupported_insns`).

## Verification

`difftest.py` runs every instruction sequence in WASM and in Unicorn on random machine states and
compares registers, flags and memory. `@prune` cases exercise dead-flag pruning (the `keep_flags`
mode hides pruning bugs). `host/lua-tests.mjs` runs real LuaJIT from GMod's `lua_shared.dll`.
The differential harness requires at least one completed execution per case, compares stack memory,
and treats unsupported/invalid cases as failures. The SIMD edge tests use architectural expectations:
Unicorn does not model the unconditional denormal flushing of RCPPS/RSQRTPS.

Pitfalls found the hard way are in the repo memory notes: intra-instruction flag reads, `push [esp+x]`,
`movsd` string-vs-SSE, undefined flags, JIT mode constants.

## Status

| Component | State |
| --- | --- |
| inspector / census / interface diff | done |
| lifter core (integer, x87, SSE/SSE2, strings, jump tables, VM regions, unwinding) | done, differential-tested |
| `lua_shared.dll` end to end in Node (CRT init, LuaJIT, errors, coroutines) | **works** (17/17) |
| `server.dll`, `client.dll` lift | see `out/*-build.log` |
| multi-DLL linking (shared register file, cross-module dispatch) | not started |
| engine side (see below) | full retail lift selected; not implemented |
| content (VPK/maps) and addons (GMA/Workshop) | not started |

## Engine architecture

1. **Hybrid:** keep the open Source-2013 engine port (wasm, clang/Itanium ABI) and lift only the
   game DLLs. Needs a generated two-way C++ ABI bridge (MSVC thiscall/vtable order vs Itanium) for
   every cross-boundary interface and for every engine-held pointer to a guest object; GMod's engine
   additions (e.g. `VEngineClient015`) must be reimplemented.
2. **Full lift (selected):** lift the whole install (engine.dll, materialsystem, shaderapidx9, vphysics,
   studiorender, vgui2, filesystem, …≈45 MB of x86). One ABI everywhere; the only boundary is the OS:
   Win32 shim (kernel32/user32/gdi32/winmm/ws2_32/advapi32/ole32…), D3D9 on WebGL2 (+ SM2/3 shader
   translation), DirectSound/WebAudio, GDI fonts via canvas, and a DOM replacement for the CEF/Awesomium
   menu. Best fidelity to "everything from the actual game"; largest OS surface.

## Memory

The lifting toolchain is memory-bounded: `build_dll.py` lifts in chunks of `--chunk-insns` x86
instructions (default 200k), assembles each chunk with binaryen and frees it. All lifted functions
share one funcref table (cross-chunk calls are `call_indirect`); CPU registers live in the core
module's globals which chunks import. `lua_shared` peaks at ~134 MB RSS (a single whole-DLL WAT string
for `server.dll` was 1.2 GB before this). Runtime memory is expected to be comparable to the existing
HL2/Portal ports. Size reference for `lua_shared`: 7.6 MB with names, 4.7 MB with `--opt` (wasm-opt -Oz),
0.9 MB gzipped.
