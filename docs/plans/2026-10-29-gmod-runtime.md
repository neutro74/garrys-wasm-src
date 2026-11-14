# GameBox GMod compatibility runtime

**Goal:** Implement the missing GMod layer on the existing Source WASM foundation, validating real Lua and addons incrementally. Full game/addon parity is the long-term goal, not an assertion about the first runtime build.

**Architecture:** Portable Lua compiled to WebAssembly, GLua parser extensions, independent client/server states, validated addon virtual filesystems, and explicit Source callbacks. Keep this work in `ports/gmod/compat` so it does not replace working HL2 binaries.

**Decision:** Native game sources are unavailable. Proceed with reimplementation, as directed by the user's correction; do not ask them to supply private Facepunch code. Reusing the HL2 alias alone cannot implement GMod. Do not introduce streaming or a substitute sandbox game.

## Current tasks

- [ ] Compile portable Lua with GLua operators, comments and loop continue; test syntax, scopes, errors and instruction limits.
- [ ] Mount GMA and loose addon files; enforce archive bounds/CRC/path safety and realm-specific autorun ordering.
- [ ] Run actual installed GMod hook/list modules in browser WASM; check real callbacks, include paths, client/server separation and failure recovery.
- [ ] Document reproducible build/test commands and remaining engine integration work.

## Next gameplay gates

Bind entity handles and VPhysics to the existing Source server, then prove map loading, colliding prop spawning, physgun pickup/freeze, weld and undo in the browser. After that bring up client Lua/Derma, scripted entities/weapons, local net messages, saves and addon compatibility suites. Workshop fetching and native modules require their own implementations. No playable catalog entry until gameplay is demonstrated.

## Verification

Use native and actual Chromium WASM tests. Generated test addons establish mount/realm behavior; installed Facepunch modules establish library compatibility. Report them separately from live gameplay. Preserve the dirty workspace and installed game. No Sites. No new game UI is required for this runtime milestone.
