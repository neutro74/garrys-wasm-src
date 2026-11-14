#!/bin/sh
# Chunked lifts of the remaining game-side DLLs the engine loads on demand.
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
build() { # name file base
  /usr/bin/time -v .venv/bin/python build_dll.py "$2" --base "$3" --out "out/$1-thread" --cross-dll --import-cpu --shared-memory \
    > "out/$1-thread-build.log" 2>&1
}
build resources "$R/garrysmod/bin/resources.dll" 0x11000000 &
build gameshader "$R/garrysmod/bin/game_shader_generic_garrysmod.dll" 0x11200000 &
build mdllib "$R/bin/mdllib.dll" 0x11400000 &
build gmodaudio "$R/bin/gmod_audio.dll" 0x11600000 &
build serverbrowser "$R/bin/ServerBrowser.dll" 0x11800000 &
build adminserver "$R/bin/AdminServer.dll" 0x11c00000 &
build holly "$R/garrysmod/bin/holly.dll" 0x11e00000 &
wait
echo done > out/extra.done
