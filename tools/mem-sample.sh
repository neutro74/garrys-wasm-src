#!/bin/sh
# Memory of the test Chromium (run/chrome-profile) by process type, in MB: resident plus swapped out, so the
# numbers hold up when the machine is under memory pressure.
root=$(pgrep -f -- 'chromium-browser.*user-data-dir=.*garrys-wasm/run/chrome-profile( |$)' | while read p; do tr '\0' ' ' < /proc/$p/cmdline | grep -q -- '--type=' || echo $p; done | head -1)
[ -z "$root" ] && { echo "no test browser"; exit 0; }
all="$root $(pstree -pT $root | grep -o '([0-9]*)' | tr -d '()')"
for p in $(echo $all | tr ' ' '\n' | sort -u); do
  [ -r /proc/$p/status ] || continue
  t=$(tr '\0' ' ' < /proc/$p/cmdline | grep -o -- '--type=[a-z-]*' | head -1); t=${t#--type=}
  printf "%s %s\n" "${t:-browser}" "$(awk '/VmRSS/{r=$2} /VmSwap/{w=$2} END{print int((r+w)/1024)}' /proc/$p/status)"
done | awk '{s[$1]+=$2; tot+=$2} END{for (k in s) if (s[k]>20) printf "%s=%d ", k, s[k]; printf "total=%d\n", tot}'
