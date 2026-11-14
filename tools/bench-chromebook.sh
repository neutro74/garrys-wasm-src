#!/bin/sh
# Approximates a Chromebook on this machine and measures gm_construct: the browser (and everything it starts) runs
# in a systemd user scope pinned to fewer cores, with a CPU quota scaled by single-thread speed, and a memory cap
# with no swap. Reports frames per second in the map and the scope's peak memory.
#
#   tools/bench-chromebook.sh [low|n4000|mid|low-cpu] [seconds]
#
#   low  Celeron N4500 class (2 cores, ~2.6x slower per core than a Ryzen 7 5800H), 4 GB: 2.5 GB for the browser
#   n4000  same CPU-quota approximation as low; does not emulate N4000 per-core speed or its UHD 600 GPU
#   mid  Core i3-1115G4 class (4 threads, ~1.25x slower), 8 GB: 5 GB for the browser
#   low-cpu  the low CPU with mid memory (to measure speed where low's memory cap stops the map loading)
#   celeron  two physical cores clocked to Celeron single-thread speed (per-core, unlike the quota; needs root)
# cpu0/cpu1 are SMT siblings of one core on this machine: two-core profiles use cpu0 and cpu2.
#
# Limits: a CPU quota slows the average, not each instruction (work runs at full speed, then waits for the next
# 100 ms period), and the GPU is this machine's, not a Chromebook's. Uses a headed window (headless Chromium is
# unfocused, which caps the engine at fps_max_nofocus).
cd "$(dirname "$0")/.." || exit 1
tier=${1:-low} seconds=${2:-240}
case $tier in
  low|n4000) cpus=0,2 quota=77% mem=2560M ;;
  mid) cpus=0-3 quota=320% mem=5G ;;
  low-cpu) cpus=0,2 quota=77% mem=5G ;;
  celeron)
    # Two physical cores at Celeron single-thread speed (no quota): the closest per-core model. Needs root, and
    # amd-pstate in passive mode (in active/EPP mode the max frequency is ignored); cap both SMT siblings:
    #   echo passive | sudo tee /sys/devices/system/cpu/amd_pstate/status
    #   sudo cpupower -c 0-3 frequency-set -g performance -u 1100MHz   (~2.5-3x slower per core than unlimited)
    # Undo: echo active | sudo tee /sys/devices/system/cpu/amd_pstate/status; sudo cpupower -c 0-3 frequency-set -u 4465MHz
    cpus=0,2 quota= mem=5G
    for c in 0 2; do f=$(cat /sys/devices/system/cpu/cpu$c/cpufreq/scaling_max_freq)
      [ "$f" -le 1800000 ] || { echo "cpu$c max is ${f} kHz: limit the clock first (see above)"; exit 3; }; done ;;
  *) echo "usage: $0 [low|n4000|mid|low-cpu|celeron] [seconds]"; exit 2 ;;
esac
out=${BENCH_OUT:-run/bench-$tier}
quotaPeriod=${BENCH_PERIOD:-100ms}
q=$(node -e 'console.log("low=1&cmd="+encodeURIComponent((process.env.BENCH_CMD||"")+" +fps_max_nofocus 0 +map gm_construct"))')
echo "bench $tier: cpus $cpus, CPUQuota $quota, MemoryMax $mem (no swap), CPU quota period $quotaPeriod, ${seconds}s"
systemd-run --user --scope --quiet ${quota:+-p CPUQuota=$quota} -p CPUQuotaPeriodSec="$quotaPeriod" -p MemoryMax=$mem -p MemorySwapMax=0 -- sh -c '
  cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
  BROWSER_NO_SCOPE=1 HEADED=1 WEB_GPU=1 LIFT_OUT="${LIFT_OUT:-tools/lift/out}" taskset -c '"$cpus"' node tools/web-test.mjs --seconds '"$seconds"' --shots 30 \
    --out '"$out"' --profile run/bench-profile --query "'"$q"'" > '"$out"'.log 2>&1
  echo "exit $?"
  echo "cpu.max: $(cat $cg/cpu.max)"
  cat $cg/cpu.stat
  echo "peak memory MB: $(( $(cat $cg/memory.peak) / 1048576 ))"
  grep -c oom_kill $cg/memory.events >/dev/null && grep oom $cg/memory.events | tr "\n" " "; echo
'
grep "frames=" "$out.log" | awk -F'frames=' '{ parts=split($2,a," elapsedMs="); f=a[1]+0; if(parts>1)t=a[2]/1000; else {split($1,b,"/t");t=b[length(b)]+0} if(n++ && t>pt)printf "%.2f fps (%.1fs)\n",(f-p)/(t-pt),t; p=f;pt=t }' | tail -12
grep -E "CRASHED|exception|FAILED" "$out.log" | head -5

grep "frame timing:" "$out.log" | tail -4
