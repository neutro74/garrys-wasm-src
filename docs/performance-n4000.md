# Chromebook 4 GB / Celeron N4000 optimization

Target: sustained 30 FPS (33.33 ms per frame), with room for ChromeOS inside 4 GB.
A desktop run or CPU quota is not validation on an N4000/UHD 600.

## Current implementation (2026-11-12)

The active workspace contains the rebuilt/optimized R3 binary groups plus the latest
host/runtime improvements. The 30 FPS requirement remains **unmet in the constrained test** and unverified on a
physical Chromebook. All measurements below use gm_construct at the explicit low
preset, a real Radeon GPU, physical desktop cores 0 and 2, aggregate CPUQuota 77%,
MemoryMax 2560 MB, no swap. The standard quota period remains 100 ms.

| Candidate | Final rolling 900-frame FPS | p95 ms | Result |
| --- | ---: | ---: | --- |
| R1 structured loops | 19.26 | 91.60 | Previous active baseline |
| R3 native host paths, leaf summaries, O3 | 24.77 | 75.29 | Binaries activated |
| R4 shared heap size classes | 25.76 | 72.46 | Retained |
| R5 core dispatcher hash | 25.61 | 73.73 | Experimental; not activated |
| R6 reusable upload arenas | 28.19 | 52.60 | Retained; peak rolling window 29.67 |
| R7 packed constants/UP payloads | 28.13 | 52.03 | Retained; peak rolling window 29.54 |
| R8 jitter recovery, bitwise transpose | 29.02 | 49.57 | Current active runtime; peak rolling window 29.70 |
| R9 larger inlining | 28.91 | 49.69 | No clear gain; not activated |

Runs have different warmups/durations, so the table is an implementation record,
not a controlled estimate of each change's isolated speedup. FPS and percentiles
come from actual render-worker WebGL submission completion timestamps. They do not
measure physical display scanout. The old shell interval report divided frame
deltas by a nominal 30 seconds; new runs use measured elapsed time instead.

R8 adds bounded small-jitter recovery in the frame pacer and allocation-free,
bit-preserving matrix transposes. Its measured steady intervals were 28.94–29.70 FPS
(`run/n4000-r8-paced-bench.log`), with actual CPU max 77000/100000 and zero OOM kills.
The same build with a separately labeled 20 ms period reached 28.56 FPS in the final
rolling window; this did not improve the target (`run/n4000-r8-period20-bench.log`).
The original 100 ms benchmark remains unchanged as the default.

The larger inlining experiment retained eight of nineteen hot modules after the
per-module size checks. Hot Wasm bytes rose from 88,501,593 to 92,887,514 (~5%). Its
300-second original-budget run did not show a clear FPS gain, so those artifacts
remain inactive in `run/out-n4000-inline256`. Selection details are recorded in
`run/n4000-r9-inline256-selection.json`.

The upload arena has three lazily allocated 4 MB slots. It snapshots guest writes
immediately, reuses a slot only after renderer acknowledgement, retains offsets
across non-present flushes, and falls back to owned transferable buffers when full.
Shader constants and UP payloads pack directly into protected storage. Persistent
renderer state copies input values before acknowledgement. Actual Chromium WebGL
buffer/texture readbacks validated shared upload views and offsets; a real-worker
1,000-frame regression validates snapshots while the consumer is delayed.

The shared allocator uses eight shared size classes plus the large-block list.
A reproduced 24,003-metadata-read traversal past 8,000 tiny free blocks is avoided.
Its 52-byte control region ends before the existing process state at 0x2040.

Current runtime validation: **34/34 commands pass** (`run/n4000-r9-runtime.log`).
Real Lua: **17/17** on both active R3 and experimental core-hash artifacts. Focused
compiler regressions: leaf synchronization 7, region branches 2, core hash 2 pass.
The older full Python suite still has the unrelated monitor-handle expectation
failure documented below; it has not been claimed green.

Active artifact inventory: `run/n4000-active-artifacts.json` (35 complete groups,
136 referenced files). Previous R1 groups and inventory are preserved in
`run/pre-n4000-r3-artifacts/`; the original pre-optimization backup remains in
`run/pre-n4000-artifacts/`. Restore matching metadata, image and code groups together.
Steam now contains Linux binaries; the matching Windows source in
`dist/gmod-web/game` remains untouched and is selected automatically when needed.
SteamCMD startup was checked successfully; no download or credentials were needed.

The existing static `dist/gmod-web` bundle now contains the active artifacts and
runtime. Previous runtime directories and assets are preserved in
`run/pre-n4000-static-runtime/`; its 7 GB matching Windows game directory was never
changed. `performance-build.json` inventories 136 artifact hashes and 112 runtime
files. The stronger inlining and core-hash experiments are not in this bundle.

A functional static-build test exposed an existing asset export error: enumeration
lowercased mixed-case loose filenames, then could not reopen files such as
`menuApp.js` on Linux. `GameFs.list` now preserves actual spelling while deduplicating
by a case-folded key and retaining search priority. All 10,753 selected UI assets
were regenerated successfully; original assets are backed up. Packaged candidate
smoke testing loaded gm_construct, accepted real keyboard/mouse input and continued
rendering with zero menu, renderer or shader exceptions. Its before/after screenshots
were visually checked. The final active bundle is also checked through Escape menu
navigation; see `run/n4000-final-static-runtime-smoke.log` for the current result.

## Work checklist

- [x] Inspect existing profiles, memory fixes, active artifacts, and game launch path.
- [x] Reproduce renderer queue race; fix unnecessary 50 ms waits after capacity becomes available.
- [x] Add first-run 960×540, 30 FPS cap, smaller shadow maps, no dynamic shadows or detail props.
- [x] Keep saved settings and explicit launch commands authoritative.
- [x] Structure single-entry engine loops without repeating the block dispatcher; preserve fallback for computed jumps and irreducible cycles.
- [x] Compare lifted instructions against x86: 472 pass, zero failures; 80 bounded random CFGs and nested/exit/irreducible execution regressions pass.
- [x] Add bounded render-worker completion intervals with p50/p95/p99/max and over-budget counts (1 ms tolerance).
- [x] Rebuild client, engine, materialsystem, shaderapidx9, and Lua; no new lifting failures.
- [x] Test actual gm_construct gameplay with the rebuilt modules.
- [x] Measure CPU-constrained, memory-limited gameplay and frame-time percentiles.
- [x] Run runtime regressions: current 34/34 commands pass.
- [x] Activate verified complete artifact groups; preserve the previous artifacts.
- [ ] Validate on a physical N4000 Chromebook.

## First-run settings

`web/launch-config.mjs` uses 960×540 (43.75% fewer pixels than 1280×720), `fps_max 30`,
512-pixel flashlight shadow maps, disabled shadows and detail props, alongside the existing
low texture/LOD/water/postprocessing defaults. Existing browser settings are retained;
`?low=1` reapplies the first-run settings. Console commands in `?cmd=...` come last.
No desktop game settings are modified. No UI redesign.

## CPU change

`tools/lift/lift.py` emits nested Wasm loops for contiguous cycles with one entry.
Forward exits branch directly to their block labels; backedges branch to a loop label.
Cycles with external entries, overlapping intervals, computed local jumps, or unsafe
unwind resume paths retain the dispatcher. No code duplication or extra runtime memory.
`LIFT_STRUCTURED_LOOPS=0` disables this pass for A/B builds.

## Measurement

`window.__frameStats` reports the last 900 render-worker completion intervals. Stalls
are included. This measures WebGL submission completion, not physical display scanout.
`tools/web-test.mjs` logs these statistics alongside frames, GPU memory, and JS heaps.
Exclude startup and loading when assessing sustained gameplay; allow 900 gameplay frames
before interpreting the rolling percentiles. A 30 FPS cap is a ceiling, not proof that a
slow CPU can reach it.

Reproduce the constrained run with:

```sh
LIFT_OUT=run/out-n4000-loops-test tools/bench-chromebook.sh n4000 240
```

`BENCH_CMD='+fps_max 0'` measures uncapped throughput. `n4000` is an explicitly
approximate alias of the existing low CPU-quota profile, not a hardware emulator.

Baseline live desktop (ABI build, renderer worker, original 1280×720 defaults):
37–39 FPS at the gm_construct spawn, 124 MB textures / 26 MB surfaces / 7 MB buffers.
Evidence: `run/n4000-before.log`, screenshots in `run/n4000-before/`.

One existing Python suite failure reproduces with loop structuring disabled:
`test_d3d9.py` expects monitor handle 0x7ff10500 but the host returns 0x7ff10600.
It is independent of the loop optimization; do not count the full suite as passing.

A Wasmtime loop microbenchmark (one million iterations, conditional body) improved from
1.20 ms to 0.81 ms median, with identical output. This is compiler evidence only,
not a gameplay or Chromebook FPS result (`run/n4000-loop-microbench.log`).

Settings/pacing-only constrained run (prior ABI binaries): final 30-second interval
17.83 FPS; peak cgroup memory 2560 MB, zero OOM kills. Final rolling completion
window: 16.41 FPS, p95 99.59 ms, p99 157.50 ms. This profile uses a 100 ms CPU quota
period, which itself introduces stalls; these percentiles are not N4000 scanout
measurements. Evidence: `run/n4000-settings-bench.log`, `run/n4000-settings.log`.

## Final constrained result

Five rebuilt hot modules + the previous verified ABI builds for the other modules,
960×540, 30 FPS cap, render worker, gm_construct spawn, 2 physical desktop CPU cores,
77% aggregate CPU quota, 2560 MB browser cap, no swap:

- Final 30-second gameplay intervals: **18.8 and 19.67 FPS**.
- Final rolling 900 render completions: **19.26 FPS**, p50 **64.78 ms**,
  p95 **91.60 ms**, p99 **106.77 ms**, max **167.91 ms**.
- Peak cgroup memory: **2560 MB**; no OOM kills.
- GPU allocation and visual checks: compare `run/bench-n4000/` and the GPU records
  in `run/bench-n4000.log`. Screenshot confirms map, world textures, HUD, and viewmodel.
- Both default and `LIFT_ABI_CALLS=1` instruction comparisons: **472 pass, zero fail**.
- Existing runtime regression command: **19/19 pass**.

**30 FPS on an N4000 is not achieved or established.** The coarse quota stalls a
fast desktop CPU and leaves the desktop GPU in use; it does not reproduce the
N4000's single-thread execution speed, GPU, thermals, or ChromeOS memory pressure.
Do not extrapolate the desktop result into a 30 FPS Chromebook guarantee. This
empty-map result also does not establish performance with NPCs, physics-heavy
contraptions, or installed addons.

The baseline's shorter run reached 17.83 FPS in its final interval, while the new
run reached 19.67 FPS after a longer warmup. Because warmup lengths differ, these
are **not a controlled percentage speedup claim**.

## Active artifacts and rollback

`tools/lift/out` now contains the verified artifact groups (35 DLLs). The prior
binary/image/metadata files are preserved in `run/pre-n4000-artifacts/`.
`run/n4000-active-artifacts.json` records all active file SHA-256 hashes and the
staged source directory. Restore the matching old `.wasm`, `.img`, and `.json`
files together from that backup to roll back the binary update. Do not combine
metadata from one build with code from another.

For existing browser preferences, launch with `?low=1` to apply the new preset;
otherwise saved preferences are retained. The static web package must be rebuilt
with `npm run package:web` into a new output directory before distributing the
updated version. No game-content package was copied as part of this optimization.

Rebuild the five optimized modules without touching the active artifacts:

```sh
NEXT=out-loops EXTRA=--abi-calls JOBS=1 tools/lift/rebuild_next.sh client engine matsys shaderdx9 lua
```

The single build job bounds peak compiler memory. The pre-existing lifter still
has unsupported functions; the pass preserved the failure sets rather than
claiming complete lifting of every function.

Final active-default desktop check (`tools/lift/out`, fresh low preset, cap 30):
30.0 FPS over consecutive gameplay samples; the profiler counted 459 frames
over its nominal 15-second interval (30.6 FPS). Screenshot
`run/profile-shot.png` was visually inspected after the run; world, viewmodel,
textures, and HUD render correctly. Evidence: `run/n4000-active-desktop.log`.
All local benchmark/profile browser and server processes have exited.

## Second pass: native host hot paths and presentation pacing

This continues the work after the request to reach 30 FPS.

- [x] Count actual host calls during gameplay. Roughly 5,500 TLS reads, 900 TLS writes,
  1,550 thread-ID requests, and 1,780 performance-counter requests per frame dominate
  the generic import traffic (`run/n4000-r2-counts.log`).
- [x] Implement a Wasm host-call bridge, including a bootstrap relay for tier0, the
  first DLL that defines the CPU globals. Ordinary unregistered imports retain their
  generic handler. `GUEST_HOSTCALL_JS=1` disables the bridge.
- [x] Keep TLS values, allocation index, and last-error value in per-thread guest
  memory so their native and JS implementations remain coherent. Bounds overflow
  retains the JS path. Native calls still honor cooperative thread suspension.
- [x] Implement native TLS/FLS access, thread ID, last-error access, QPC integer
  conversion, and QPC frequency. QPC still samples the real monotonic clock; it does
  not cache, smooth, accelerate, or fabricate guest time.
- [x] Verify registers, stack cleanup, 0–13 argument callbacks, memory growth,
  overflow fallback, JS/native TLS coherence, and real guest-worker TLS isolation.
- [x] Optimize static forward edges in the Lua VM region while retaining arbitrary
  computed entry points. Two region tests and 17/17 real Lua tests pass.
- [x] Stage O3 + bounded inlining for the five hot DLLs. All optimized modules are
  smaller (no size-growth fallback needed). Staging: `run/out-n4000-o3`.
- [x] Add worker presentation deadlines using blocking waits, replacing the engine's
  Windows busy-wait limiter for the default 30 FPS preset. Explicit `+fps_max` launch
  commands retain the engine limiter and disable the host limiter.
- [x] Runtime regressions after these changes: 21/21 commands pass.
- [ ] Complete constrained gameplay measurement of the combined changes.
- [ ] Activate the combined artifacts only after gameplay verification.

The initial bridge for D3D callbacks alone measured no FPS gain. Native TLS/thread
hot paths subsequently reached roughly 21 FPS before profiling versus roughly 19
for the prior build. Each CPU profile itself reduces throughput; these intermediate
runs do not establish 30 FPS. Intermediate logs: `run/n4000-r2-{baseline,bridge,native}.log`.

The default Source command line now sets `fps_max 0`; `targetFps:30` in the host
controls presentation with `FramePacer`. The game still simulates according to
actual elapsed monotonic time. The pacer performs blocking waits rather than a
clock-polling spin and avoids bursts of catch-up frames after stalls. Saved video
quality/resolution remains authoritative; explicit FPS launch commands override
host pacing. The preset's 960×540 defaults remain first-run / `?low=1` only.

Reproduce stronger optimization in a fresh directory:

```sh
node tools/optimize-lift.mjs --out run/out-optimized-new --lua tools/lift/out-region/lua-thread
```

The tool limits each compiler to 2200 MB, two compiler threads, and a 15% maximum
binary-size expansion. It stages complete metadata/image/code groups and leaves
active inputs unchanged. New artifact groups must still be tested before activation.

## Findings, 2026-11-12 (Claude)

Measured on the desktop (Radeon, headless Chromium) with `tools/web-profile.mjs`, steady state in gm_construct:

- Frame pacer idle share is the reproducible proxy for CPU headroom: **43% idle at 30 FPS** with normal V8 tiering,
  but only **16% idle** with `--js-flags=--liftoff-only`. Un-tiered (baseline) wasm costs about 1.5x the CPU, so on a
  slow 2-core device the time until V8 has optimized the hot functions (its compile threads also use the CPU quota)
  directly lowers FPS. No fix found: V8's wasm code cache did not shorten warm-up on a second run with the same profile.
- Hot wasm functions at small indices in every module (`wasm-function[10]`/`[11]`) are the per-module indirect-call
  dispatchers (br_table over guest addresses), about 9% of busy time in total. The earlier hashed dispatcher (R5)
  gave no gain. `rep movs/stos` are emitted as per-element loops (`lift_ext.py:string_op`); bulk-memory fast paths
  are a possible lifter change but need a full rebuild of all modules and were not tried.
- About 10% of guest time at map load is blocked in synchronous Range reads (`web-reader.mjs` -> XHR `send`).
- `tools/bench-chromebook.sh low 300` (2 cores, 77% quota, 2.5 GB cap) is **noisy on this shared machine**: run 1
  reached 29 FPS from ~2.5 min then fell to ~21 after 4 min; an identical second run settled near 15 FPS.
  Peak memory sat at the 2560 MB cap in both. Use it only for large effects and repeat runs.
- Not measured: any real Chromebook. The user reports ~10 FPS there.

## GPU-bound investigation (2026-11-12)

Uncapped (`+fps_max 0`) gm_construct, low preset, desktop Radeon: the guest worker is 100% busy at ~57 fps
(~17 ms/frame) while the render worker spends only ~2 ms/frame applying ops (`?perf=1` now shows this as
"renderer thread"; `tools/web-profile.mjs` prints it). The game thread, not the GPU, is the limit.
Guest time: ~78% lifted game code (engine 18, client 17, shaderdx9 12, matsys 11, lua 7), ~17% host D3D marshalling.

- **Queued material system (`mat_queue_mode 2`) is not viable as a small change.** `+mat_queue_mode 2` has no effect
  (same fps and profile). The structural blocker is that D3D9 device state lives in the main guest worker's JS heap;
  `createThread` leaves host-bound callbacks unresolved in thread workers, and `GetSystemInfo` reports one CPU.
  A real fix moves `d3d9.mjs`/`d3dx9` into a dedicated worker with RPC from the engine thread. On a 2-core Chromebook
  this redistributes rather than reduces CPU work, so it was not pursued.
- **R5 core dispatch hash re-measured uncapped:** 54.8 vs 56.8 fps, no gain (dispatcher is ~15% of guest time).
- **Binary op stream (done):** draws and state deltas are encoded straight from device state into one transferable word
  buffer per frame (`tools/lift/host/op-stream.mjs`, `OpBackend.encodeState`), replacing per-op objects and the
  structured clone in `postMessage`. Vertex declarations are interned by id (also fixes the renderer's per-declaration
  cache being defeated by a fresh clone per change). `D3D9Renderer` is unchanged; `decodeWords` rebuilds the same
  delta/call shapes. `d3d9-backend.mjs` fell from 8.3% to 3.6% of guest time; uncapped 56.8-58.2 -> 59.3 fps
  (`run/uncapped-binops.log`). `tools/lift/host/test-op-stream.mjs` checks equivalence with the object path over
  6,800 randomized records (with and without dirty tracking). `WorkerBackend` option `binaryOps:false` restores the old path.
  Not applied to `dist/gmod-web` (static bundle) yet.

Static bundle `dist/gmod-web` refreshed with the binary op stream (web/main.js, web/render-worker.js,
tools/lift/host/d3d9-backend.mjs, op-stream.mjs, test-op-stream.mjs); previous copies in `run/pre-binops-static/`.
`tools/test-static-runtime.mjs` passes (map load, input, rendering, Escape menu, zero renderer errors). Not published to the CDN.
