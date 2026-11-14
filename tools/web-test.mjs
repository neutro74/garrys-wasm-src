#!/usr/bin/env node
// Runs the in-browser host in headless Chromium against the dev server and saves screenshots.
//   [HEADED=1] [WEB_GPU=1] node tools/web-test.mjs [--seconds 60] [--shots 20] [--out run/web] [--query "map=..."] [--profile run/chrome-profile]
// Two agents can test at once with different --out and --profile directories.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './lib/browser.mjs';

const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d; };
const seconds = +arg('seconds', 60), every = +arg('shots', 20), out = path.resolve(arg('out', 'run/web')), query = arg('query', '');
const port = 8100 + Math.floor(Math.random() * 500);
fs.rmSync(out, { recursive: true, force: true }); fs.mkdirSync(out, { recursive: true });

const server = spawn('node', [path.join(path.dirname(fileURLToPath(import.meta.url)), '../web/server.mjs'), '--port', String(port)], { stdio: ['ignore', 'inherit', 'inherit'] });
await new Promise((r) => setTimeout(r, 2000));
// A persistent profile on disk (not tmpfs): it keeps V8's compiled-wasm cache between runs.
const profile = path.resolve(arg('profile', 'run/chrome-profile')); fs.mkdirSync(profile, { recursive: true });
const browser = await launch({ width: 1280, height: 720, gpu: process.env.WEB_GPU === '1', profile, headed: process.env.HEADED === '1' });
browser.on('Runtime.consoleAPICalled', (p) => console.log(`[page] ${p.args.map((a) => a.value ?? a.description).join(' ')}`));
browser.on('Runtime.exceptionThrown', (p) => console.log(`[page] exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`));
// Console messages also arrive through the Log domain; only keep Log entries that are not console output.
browser.on('Log.entryAdded', (p) => { if (p.entry.level !== 'verbose' && p.entry.source !== 'console-api' && p.entry.source !== 'worker') console.log(`[chromium:${p.entry.level}] ${p.entry.text}`); });
await browser.send('Log.enable');
browser.on('Inspector.targetCrashed', async () => {
  console.log('[test] PAGE CRASHED');
  await browser.close().catch(() => {});
  server.kill();
  process.exit(2);
});
await browser.send('Inspector.enable').catch(() => {});
await browser.goto(`http://127.0.0.1:${port}/?${query}`);
const t0 = Date.now();
while ((Date.now() - t0) / 1000 < seconds) {
  await new Promise((r) => setTimeout(r, every * 1000));
  const file = path.join(out, `t${String(Math.round((Date.now() - t0) / 1000)).padStart(4, '0')}.png`);
  await Promise.race([browser.screenshot(file), new Promise((r) => setTimeout(r, 20000))]);
  const frames = await browser.eval('window.__frames?.() ?? -1');
  console.log(`[test] ${file} frames=${frames} elapsedMs=${Date.now()-t0}`);
  const timing = await browser.eval('window.__frameStats ?? null');
  if (timing) console.log(`[test] frame timing: ${JSON.stringify(timing)}`);
  const gpu = await browser.eval('window.__gpu?.() ?? null');
  if (gpu) console.log(`[test] gpu MB: texture ${(gpu.texture / 1048576).toFixed(0)}, surface ${(gpu.surface / 1048576).toFixed(0)}, buffer ${(gpu.buffer / 1048576).toFixed(0)}; live ${JSON.stringify(gpu.live)}; stats ${JSON.stringify(gpu.stats)}`);
  if (gpu?.byFormat) console.log(`[test] textures by format MB: ${Object.entries(gpu.byFormat).filter(([, v]) => v > 4 << 20).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${(v / 1048576).toFixed(0)}`).join(', ')}`);
  const heaps = await browser.heaps();
  console.log(`[test] js heaps MB: ${heaps.map((h) => `${h.url} ${h.usedMB.toFixed(0)}`).join(', ')} (total ${heaps.reduce((n, h) => n + h.usedMB, 0).toFixed(0)})`);
  console.log(`[test] panels ${JSON.stringify(await browser.eval('window.__panels?.() ?? null'))}`);
}
await browser.close();
server.kill();
process.exit(0);
