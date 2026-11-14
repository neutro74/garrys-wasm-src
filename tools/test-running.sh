#!/bin/sh
# Prints how many Chromium processes use the given profile dir (default run/chrome-profile).
profile=$(realpath -m "${1:-run/chrome-profile}")
n=0
for p in $(pgrep -x chromium-browse); do
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q -- "user-data-dir=$profile" && n=$((n + 1))
done
echo $n
