#!/bin/sh
# Rebuilds DLLs from run_all.sh into out-next/ (three at a time), leaving out/ untouched until swap_next.sh.
#   [NEXT=dir] [EXTRA="--flag"] ./rebuild_next.sh [name...]   (names as in run_all.sh; default: all)
cd "$(dirname "$0")"
R="${GMOD:-$HOME/.steam/steam/steamapps/common/GarrysMod}"
NEXT=${NEXT:-out-next}; mkdir -p $NEXT/logs
names=" $* "
grep "^build " run_all.sh | sed 's/ &$//' | while read -r _ n f b t rest; do
  [ $# -gt 0 ] && case "$names" in *" $n "*) ;; *) continue ;; esac
  f=$(eval echo "$f")
  echo ".venv/bin/python build_dll.py $f --base $b --thunk-base $t --out $NEXT/$n-thread $rest $EXTRA > $NEXT/logs/$n.log 2>&1 && echo done $n || echo FAILED $n"
done | xargs -P "${JOBS:-3}" -I{} sh -c '{}'
echo ALL_DONE
