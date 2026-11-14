#!/bin/sh
# Moves a finished out-next/ build into out/ (each DLL's files together).
cd "$(dirname "$0")"
grep -q FAILED out-next/build.out && { echo "build has failures; not swapping"; exit 1; }
for j in out-next/*-thread.json; do
  n=$(basename "$j" .json)
  rm -f out/"$n".*
  mv out-next/"$n".* out/
done
echo swapped
