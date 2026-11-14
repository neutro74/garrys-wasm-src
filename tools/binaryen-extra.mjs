#!/usr/bin/env node
// Applies extra Binaryen passes to already-optimized lifted artifacts, into a NEW staging directory (the source is
// never modified). Jobs run in parallel, each memory-limited.
//   node tools/binaryen-extra.mjs --source tools/lift/out --out run/out-bin-exp [--jobs 3] [--only client,engine] \
//        [--passes "--low-memory-unused --optimize-added-constants-propagate -O3"]
// The default pass set folds `ptr + constant` address arithmetic into load/store immediates. The lifter emits every
// guest address as an explicit i32.add, which V8 cannot fold itself. This assumes the guest never relies on a
// 32-bit wrap-around inside an address calculation, so validate the output with the runtime tests before activating it.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';

const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i < 0 ? d : process.argv[i + 1]; };
const source = path.resolve(arg('source', 'tools/lift/out')), out = path.resolve(arg('out', 'run/out-bin-exp'));
const jobs = +arg('jobs', 3), only = arg('only', '') ? new Set(arg('only').split(',')) : null;
const passes = arg('passes', '--low-memory-unused --optimize-added-constants-propagate -O3').split(/\s+/).filter(Boolean);
if (fs.existsSync(out)) throw Error('Output exists; choose a fresh staging directory');
fs.mkdirSync(out, { recursive: true });

const files = new Set(), wasm = [];
for (const name of fs.readdirSync(source).filter(n => n.endsWith('-thread.json'))) {
  const m = JSON.parse(fs.readFileSync(path.join(source, name)));
  for (const f of [name, name.replace('.json', '.img'), m.core, ...m.chunks]) files.add(f);
}
for (const f of files) {
  fs.copyFileSync(path.join(source, f), path.join(out, f), fs.constants.COPYFILE_FICLONE);
  const mod = f.replace(/-thread.*$/, '');
  if (f.endsWith('.wasm') && (!only || only.has(mod))) wasm.push(f);
}
const features = ['--enable-tail-call', '--enable-simd', '--enable-bulk-memory', '--enable-mutable-globals', '--enable-sign-ext', '--enable-nontrapping-float-to-int', '--enable-threads'];
let failures = 0, saved = 0, done = 0;
const optimize = f => new Promise(resolve => {
  const tmp = path.join(out, f + '.opt'), before = fs.statSync(path.join(out, f)).size;
  const p = spawn('systemd-run', ['--user', '--scope', '--quiet', '-p', 'MemoryMax=2200M', '-p', 'MemorySwapMax=0', '--', 'env', 'BINARYEN_CORES=2',
    path.resolve('third_party/binaryen/wasm-opt'), path.join(out, f), ...features, ...passes, '-o', tmp], { stdio: ['ignore', 'ignore', 'pipe'] });
  let err = ''; p.stderr.on('data', d => { err += d; });
  p.on('exit', code => {
    done++;
    if (code !== 0) { failures++; console.log('FAILED', f, code, err.slice(0, 200)); try { fs.unlinkSync(tmp); } catch {} return resolve(); }
    const after = fs.statSync(tmp).size;
    fs.renameSync(tmp, path.join(out, f)); saved += before - after;
    console.log(`DONE ${done}/${wasm.length} ${f} ${before} -> ${after}`);
    resolve();
  });
});
const queue = [...wasm];
await Promise.all(Array.from({ length: jobs }, async () => { for (let f; (f = queue.shift());) await optimize(f); }));
console.log(failures ? `FAILED_MODULES ${failures}` : `ALL_DONE saved ${(saved / 1048576).toFixed(1)} MiB`);
process.exitCode = failures ? 1 : 0;
