#!/bin/sh
# Runs wasm-opt -O1 over every module out/ references, writing complete DLL sets to out-next/ for swap_next.sh.
cd "$(dirname "$0")"
mkdir -p out-next
F="--enable-tail-call --enable-simd --enable-bulk-memory --enable-mutable-globals --enable-sign-ext --enable-nontrapping-float-to-int --enable-threads"
for j in out/*-thread.json; do
  n=$(basename "$j" .json)
  cp out/"$n".json out/"$n".img out-next/ 2>/dev/null
  node -e 'const j=require("./"+process.argv[1]); for (const f of [j.core, ...j.chunks]) console.log(f)' "$j"
done | xargs -P "${JOBS:-3}" -I{} sh -c "../../third_party/binaryen/wasm-opt $F -O1 out/{} -o out-next/{} && echo done {} || echo FAILED {}"
echo ALL_DONE
