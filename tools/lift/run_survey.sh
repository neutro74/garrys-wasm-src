#!/bin/sh
# Detached survey of the GMod DLLs; results land in tools/lift/out/. Safe to run after closing Claude.
cd "$(dirname "$0")"
B="${GMOD_BIN:-$HOME/.steam/steam/steamapps/common/GarrysMod/garrysmod/bin}"
PY=.venv/bin/python
for f in lua_shared client server; do
  $PY census.py "$B/$f.dll" --top 80 --json out/$f-census.json > out/$f-census.txt 2>&1
done
$PY inspect_pe.py "$B/client.dll" "$B/server.dll" "$B/lua_shared.dll" --engine ../../ports/gmod/engine --json out/inspect.json --quiet
echo done > out/survey.done
