#!/bin/sh
# Detached chunked lift of the big GMod DLLs. Results + peak RSS in tools/lift/out/{server,client}-build.log
cd "$(dirname "$0")"
G="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
for spec in "server:$G/garrysmod/bin/server.dll:0x08000000" "client:$G/garrysmod/bin/client.dll:0x0c000000"; do
  name=${spec%%:*}; rest=${spec#*:}; dll=${rest%%:*}; base=${rest##*:}
  /usr/bin/time -v .venv/bin/python build_dll.py "$dll" --base $base --out out/$name > out/$name-build.log 2>&1
done
echo done > out/big.done
