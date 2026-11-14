#!/bin/sh
# Detached chunked lifts of server.dll / client.dll with cross-DLL + shared-CPU + shared-memory flags.
cd "$(dirname "$0")"
G="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}/garrysmod/bin"
for spec in "server:server.dll:0x08000000" "client:client.dll:0x12000000"; do
  name=${spec%%:*}; rest=${spec#*:}; dll=${rest%%:*}; base=${rest##*:}
  /usr/bin/time -v .venv/bin/python build_dll.py "$G/$dll" --base $base --out out/$name-thread --cross-dll --import-cpu --shared-memory \
    > out/$name-thread-build.log 2>&1
done
echo done > out/game.done
