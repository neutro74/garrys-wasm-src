#!/bin/sh
# Stops the test Chromium using one profile (default run/chrome-profile) and clears its stale locks.
#   tools/kill-tests.sh [profile-dir]
cd "$(dirname "$0")/.."
profile=$(realpath -m "${1:-run/chrome-profile}")
for p in $(pgrep -f -- "user-data-dir=$profile( |$)"); do kill "$p" 2>/dev/null; done
sleep 1
rm -f "$profile/SingletonLock" "$profile/SingletonSocket" "$profile/SingletonCookie"
