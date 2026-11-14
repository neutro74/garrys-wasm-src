#!/bin/sh
# tools/mem-watchdog.sh <profile-substring> <log>: logs AMD GTT and MemAvailable every 2 s; kills that test's browser
# if available memory drops below 1.2 GB or GTT passes 3.5 GB (GPU memory is not charged to cgroups).
pat=$1 log=$2
while :; do
  gtt=$(( $(cat /sys/class/drm/card1/device/mem_info_gtt_used) / 1048576 ))
  avail=$(( $(awk '/MemAvailable/{print $2}' /proc/meminfo) / 1024 ))
  pids=$(pgrep -f "chromium-browser.*$pat")
  echo "$(date +%T) gtt=${gtt}MB avail=${avail}MB" >> "$log"
  if [ -n "$pids" ] && { [ "$avail" -lt 1200 ] || [ "$gtt" -gt 3500 ]; }; then
    echo "$(date +%T) WATCHDOG KILL gtt=${gtt} avail=${avail}" >> "$log"; kill -9 $pids; fi
  sleep 2
done
