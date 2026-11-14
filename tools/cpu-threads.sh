#!/bin/sh
# CPU used per Chromium thread over a window, grouped by process type and thread name (for finding where a game
# frame's CPU goes across the renderer, its workers, the GPU process and the browser).
#   tools/cpu-threads.sh <profile-dir-substring> [seconds]
pat=$1 secs=${2:-30}
snap() {
  # The browser process carries the profile path; its renderer/GPU/utility children are found through the tree.
  root=$(pgrep -o -f "[c]hromium-browser.*--user-data-dir=[^ ]*$pat")   # [c]: never matches this shell's own command line
  [ -n "$root" ] || return
  pids=$(ps -eo pid=,ppid= | awk -v r="$root" '{ parent[$1]=$2 } END { for (p in parent) { q=p; while (q && q!=r) q=parent[q]; if (q==r) print p } }')
  for pid in $pids; do
    type=$(tr '\0' '\n' < /proc/$pid/cmdline 2>/dev/null | sed -n 's/^--type=//p' | head -1)
    for t in /proc/$pid/task/*; do
      [ -r "$t/stat" ] || continue
      # comm may contain spaces: fields after the closing parenthesis; utime=14, stime=15 overall.
      awk -v type="${type:-browser}" -v tid="${t##*/}" '{ s=$0; i=index(s,"("); j=index(s,") "); comm=substr(s,i+1,j-i-1);
        split(substr(s,j+2),f," "); print type"|"comm"|"tid, f[12]+f[13] }' "$t/stat"
    done
  done
}
a=$(mktemp) b=$(mktemp)
snap > "$a"; sleep "$secs"; snap > "$b"
hz=$(getconf CLK_TCK)
awk -v individual="${CPU_THREADS_INDIVIDUAL:-0}" -v hz="$hz" -v secs="$secs" 'NR==FNR { t0[$1]=$2; next } { d=$2-t0[$1]; if (d<0) d=0; split($1,parts,"|"); key=parts[1]" / "parts[2]; if(individual==1)key=key" / "parts[3]; sum[key]+=d; total+=d }
  END { for (k in sum) if (sum[k]>0) printf "%6.1f%% of a core  %s\n", 100*sum[k]/hz/secs, k; printf "%6.1f%% total\n", 100*total/hz/secs }' "$a" "$b" | sort -rn | head -20
rm -f "$a" "$b"
