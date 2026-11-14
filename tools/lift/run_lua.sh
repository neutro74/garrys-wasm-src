#!/bin/sh
# lua_shared (LuaJIT 2.0.4). It must not see SSE2: LuaJIT then disables its JIT itself, which is required
# because the JIT emits x86 at run time and the lifted module cannot execute generated code.
cd "$(dirname "$0")"
G="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
.venv/bin/python build_dll.py "$G/garrysmod/bin/lua_shared.dll" --base 0x400000 --out out/lua-thread \
  --vm-region 0x1000,0x57e0 --cross-dll --import-cpu --shared-memory --cpuid-clear-edx 0x4000000
