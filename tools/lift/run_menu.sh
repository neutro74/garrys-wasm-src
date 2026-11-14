#!/bin/sh
# Chunked lifts of the menu DLLs (same flags as the other *-thread builds).
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
build() { # name file base
  /usr/bin/time -v .venv/bin/python build_dll.py "$2" --base "$3" --out "out/$1-thread" --cross-dll --import-cpu --shared-memory \
    > "out/$1-thread-build.log" 2>&1
}
build menusystem "$R/garrysmod/bin/menusystem.dll" 0x10a00000 &
build htmlstub "$R/bin/html_stub.dll" 0x10d00000 &
wait
echo done > out/menu.done
