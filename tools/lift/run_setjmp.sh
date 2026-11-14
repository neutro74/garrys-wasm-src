#!/bin/sh
# DLLs that call the CRT's setjmp: lifted with setjmp/longjmp resume support (see SETJMP_CONT in lift.py).
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
build() { # name file base
  /usr/bin/time -v .venv/bin/python build_dll.py "$2" --base "$3" --out "out/$1-thread" --cross-dll --import-cpu --shared-memory \
    > "out/$1-thread-build.log" 2>&1
}
build vphysics "$R/bin/vphysics.dll" 0xee00000 &
build engine "$R/bin/engine.dll" 0xa000000 &
build gameui "$R/bin/GameUI.dll" 0xf200000 &
build vstdlib "$R/bin/vstdlib.dll" 0x7400000 &
build resources "$R/garrysmod/bin/resources.dll" 0x11000000 &
wait
echo done > out/setjmp.done
