#!/bin/sh
# Detached chunked lifts of the remaining launcher-loaded DLLs (same flags as the *-thread builds).
cd "$(dirname "$0")"
G="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}/bin"
build() { # name file base
  /usr/bin/time -v .venv/bin/python build_dll.py "$G/$2" --base "$3" --out "out/$1-thread" --cross-dll --import-cpu --shared-memory \
    > "out/$1-thread-build.log" 2>&1
}
build launcher launcher.dll 0x10000000 &
build video video_services.dll 0x10100000 &
build soundemitter soundemittersystem.dll 0x10200000 &
build soundsystem soundsystem.dll 0x10400000 &
build scenefilecache scenefilecache.dll 0x10800000 &
build vaudio vaudio_speex.dll 0x10900000 &
wait
echo done > out/more.done
