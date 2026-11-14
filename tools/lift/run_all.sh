#!/bin/sh
# Rebuild every game DLL with a unique thunk range in the reserved region 0x13000000+ (see THUNK_REGION).
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
mkdir -p out/logs

build() { # name file base thunk extra...
  n=$1; f=$2; b=$3; t=$4; shift 4
  /usr/bin/time -v .venv/bin/python build_dll.py "$f" --base "$b" --thunk-base "$t" --out "out/$n-thread" "$@" > "out/logs/$n.log" 2>&1
}

build client "$R/garrysmod/bin/client.dll" 0x12000000 0x13010000 --cross-dll --import-cpu --shared-memory --opt &
build engine "$R/bin/engine.dll" 0xa000000 0x13030000 --cross-dll --import-cpu --shared-memory --opt &
build gameui "$R/bin/GameUI.dll" 0xf200000 0x13060000 --cross-dll --import-cpu --shared-memory --opt &
build matsys "$R/bin/materialsystem.dll" 0xc000000 0x130d0000 --cross-dll --import-cpu --shared-memory --opt &
wait
build menusystem "$R/garrysmod/bin/menusystem.dll" 0x10a00000 0x130f0000 --cross-dll --import-cpu --shared-memory --opt &
build server "$R/garrysmod/bin/server.dll" 0x8000000 0x13120000 --cross-dll --import-cpu --shared-memory --opt &
build studiorender "$R/bin/studiorender.dll" 0xe500000 0x13190000 --cross-dll --import-cpu --shared-memory --opt &
build vphysics "$R/bin/vphysics.dll" 0xee00000 0x13200000 --cross-dll --import-cpu --shared-memory --opt &
wait
build adminserver "$R/bin/AdminServer.dll" 0x11c00000 0x13000000 --cross-dll --import-cpu --shared-memory --opt &
build datacache "$R/bin/datacache.dll" 0xed00000 0x13020000 --cross-dll --import-cpu --shared-memory --opt &
build filesystem "$R/bin/filesystem_stdio.dll" 0x7a00000 0x13040000 --cross-dll --import-cpu --shared-memory --opt &
build gameshader "$R/garrysmod/bin/game_shader_generic_garrysmod.dll" 0x11200000 0x13050000 --cross-dll --import-cpu --shared-memory --opt &
wait
build gmodaudio "$R/bin/gmod_audio.dll" 0x11600000 0x13070000 --cross-dll --import-cpu --shared-memory --opt &
build holly "$R/garrysmod/bin/holly.dll" 0x11e00000 0x13080000 --cross-dll --import-cpu --shared-memory --opt &
build htmlstub "$R/bin/html_stub.dll" 0x10d00000 0x13090000 --cross-dll --import-cpu --shared-memory --opt &
build input "$R/bin/inputsystem.dll" 0x7800000 0x130a0000 --cross-dll --import-cpu --shared-memory --opt &
wait
build launcher "$R/bin/launcher.dll" 0x10000000 0x130b0000 --cross-dll --import-cpu --shared-memory --opt &
build lua "$R/garrysmod/bin/lua_shared.dll" 0x400000 0x130c0000 --cross-dll --import-cpu --shared-memory --vm-region 0x1000,0x57e0 --cpuid-clear-edx 0x4000000 --opt &
build mdllib "$R/bin/mdllib.dll" 0x11400000 0x130e0000 --cross-dll --import-cpu --shared-memory --opt &
build resources "$R/garrysmod/bin/resources.dll" 0x11000000 0x13100000 --cross-dll --import-cpu --shared-memory --opt &
wait
build scenefilecache "$R/bin/scenefilecache.dll" 0x10800000 0x13110000 --cross-dll --import-cpu --shared-memory --opt &
build serverbrowser "$R/bin/ServerBrowser.dll" 0x11800000 0x13130000 --cross-dll --import-cpu --shared-memory --opt &
build shaderdx9 "$R/bin/shaderapidx9.dll" 0xe200000 0x13140000 --cross-dll --import-cpu --shared-memory --opt &
build soundemitter "$R/bin/soundemittersystem.dll" 0x10200000 0x13150000 --cross-dll --import-cpu --shared-memory --opt &
wait
build soundsystem "$R/bin/soundsystem.dll" 0x10400000 0x13160000 --cross-dll --import-cpu --shared-memory --opt &
build stdshader "$R/bin/stdshader_dx9.dll" 0xe400000 0x13170000 --cross-dll --import-cpu --shared-memory --opt &
build steam "$R/bin/steam_api.dll" 0x7600000 0x13180000 --cross-dll --import-cpu --shared-memory --opt &
build tier0 "$R/bin/tier0.dll" 0x7000000 0x131a0000 --cross-dll --shared-memory --opt &
wait
build unicode "$R/bin/unicode.dll" 0xf400000 0x131b0000 --cross-dll --import-cpu --shared-memory --opt &
build vaudio "$R/bin/vaudio_speex.dll" 0x10900000 0x131c0000 --cross-dll --import-cpu --shared-memory --opt &
build vgui-surface "$R/bin/vguimatsurface.dll" 0xf000000 0x131d0000 --cross-dll --import-cpu --shared-memory --opt &
build vgui2 "$R/bin/vgui2.dll" 0xef00000 0x131e0000 --cross-dll --import-cpu --shared-memory --opt &
wait
build video "$R/bin/video_services.dll" 0x10100000 0x131f0000 --cross-dll --import-cpu --shared-memory --opt &
build vstdlib "$R/bin/vstdlib.dll" 0x7400000 0x13210000 --cross-dll --import-cpu --shared-memory --opt &
wait
echo done > out/all.done
