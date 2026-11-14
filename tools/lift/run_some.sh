#!/bin/sh
# Rebuild every game DLL with a unique thunk range in the reserved region 0x13000000+ (see THUNK_REGION).
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
mkdir -p out/logs

build() { # name file base thunk extra...
  n=$1; f=$2; b=$3; t=$4; shift 4
  /usr/bin/time -v .venv/bin/python build_dll.py "$f" --base "$b" --thunk-base "$t" --out "out/$n-thread" "$@" > "out/logs/$n.log" 2>&1
}
build server "$R/garrysmod/bin/server.dll" 0x8000000 0x13120000 --cross-dll --import-cpu --shared-memory &
build client "$R/garrysmod/bin/client.dll" 0x12000000 0x13010000 --cross-dll --import-cpu --shared-memory &
build engine "$R/bin/engine.dll" 0xa000000 0x13030000 --cross-dll --import-cpu --shared-memory &
build vphysics "$R/bin/vphysics.dll" 0xee00000 0x13200000 --cross-dll --import-cpu --shared-memory &
wait
build menusystem "$R/garrysmod/bin/menusystem.dll" 0x10a00000 0x130f0000 --cross-dll --import-cpu --shared-memory &
build matsys "$R/bin/materialsystem.dll" 0xc000000 0x130d0000 --cross-dll --import-cpu --shared-memory &
build gameui "$R/bin/GameUI.dll" 0xf200000 0x13060000 --cross-dll --import-cpu --shared-memory &
build studiorender "$R/bin/studiorender.dll" 0xe500000 0x13190000 --cross-dll --import-cpu --shared-memory &
wait
wait
echo done > out/some.done
