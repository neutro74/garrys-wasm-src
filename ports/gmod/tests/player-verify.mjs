// Verifies the packed GMod entry through the real player page: serves packs/ + runtime/ the way
// the CDN does (tools/serve-packs.mjs), opens runtime/player.html?game=gmod&map=gm_construct in
// headless Chromium, and records console, screenshots and memory.
//
//   scripts/heavy.sh node ports/gmod/tests/player-verify.mjs
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { launch } from '../../../../tools/lib/browser.mjs';

const ROOT = path.resolve(import.meta.dirname, '../../..');
const out = path.join(ROOT, 'ports/gmod/evidence');
const shots = path.join(ROOT, 'ports/gmod/shots');
const shotMirror = path.join(ROOT, 'screenshots/gmod');
fs.mkdirSync(out, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
fs.mkdirSync(shotMirror, { recursive: true });

const port = Number(process.env.GMOD_PORT || 8443);
const seconds = Number(process.env.GMOD_SECONDS || 200);
const index = JSON.parse(fs.readFileSync(path.join(ROOT, 'packs/index.json'), 'utf8'));
if (!index.gmod) throw new Error('packs/index.json has no gmod entry: run tools/pack.mjs --game gmod');
console.log('index.gmod =', JSON.stringify(index.gmod));

const server = spawn(process.execPath, ['tools/serve-packs.mjs', '--port', String(port)], {
  cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'],
});
server.stderr.on('data', (d) => process.stderr.write('[serve-packs] ' + d));
await new Promise((resolve, reject) => {
  server.stdout.once('data', resolve);
  server.once('error', reject);
  server.once('exit', (code) => reject(new Error('serve-packs exited ' + code)));
});

const browser = await launch({ gpu: true, profile: path.join(ROOT, 'ports/gmod/tmp/chrome-player-' + Date.now()) });
const lines = [];
browser.on('Runtime.consoleAPICalled', ({ args }) => {
  const line = args.map((a) => a.value ?? a.description ?? '').join(' ');
  lines.push(line);
  if (!/^\[(debug|verbose)\]/.test(line)) console.log(line.slice(0, 300));
});
browser.on('Runtime.exceptionThrown', ({ exceptionDetails: e }) => {
  const line = '[exception] ' + (e.exception?.description ?? e.text);
  lines.push(line);
  console.log(line);
});

const start = Date.now();
await browser.goto(`http://127.0.0.1:${port}/runtime/player.html?game=gmod&map=gm_construct`);
const markers = {};
let shotsTaken = 0;
let lastShot = 0;
const shot = async (name) => {
  const file = path.join(shots, `${name}.png`);
  await browser.screenshot(file).catch((e) => console.log('[shot failed]', e.message));
  if (fs.existsSync(file)) {
    fs.copyFileSync(file, path.join(shotMirror, path.basename(file)));
    console.log('[shot]', file);
  }
};
while (Date.now() - start < seconds * 1000) {
  await new Promise((r) => setTimeout(r, 1000));
  const text = lines.join('\n');
  for (const [name, re] of Object.entries({
    pluginLoaded: /\[gmod\] Source compatibility plugin loaded/,
    luaActivated: /\[gmod\] Lua server activated/,
    sandboxReady: /\[gmod-sandbox\] READY/,
    hostMounted: /\[gmod-host\] mounted/,
    payload: /\[gmod-host\] runtime payload/,
    map: /Map: gm_construct/,
    prop: /\[gmod-sandbox\] demo prop|\[gmod-sandbox\] spawned/,
    hook: /\[gmod-sandbox\] hook dispatch/,
    error: /failed|Could not load|\[abort\]|The game stopped/i,
  })) if (!markers[name] && re.test(text)) { markers[name] = true; console.log('[marker]', name); }
  if (markers.sandboxReady && !markers.readyShot) { markers.readyShot = true; await shot(`player-${String(shotsTaken++).padStart(2, '0')}-ready`); }
  else if (markers.prop && !markers.propShot) { markers.propShot = true; await new Promise((r) => setTimeout(r, 1500)); await shot(`player-${String(shotsTaken++).padStart(2, '0')}-props`); }
  else if (Date.now() - lastShot > 25000) { lastShot = Date.now(); await shot(`player-${String(shotsTaken++).padStart(2, '0')}-t${Math.round((Date.now() - start) / 1000)}`); }
  if (markers.prop && markers.map && markers.sandboxReady && Date.now() - start > 45000) break;
}
await shot(`player-${String(shotsTaken++).padStart(2, '0')}-final`);

let memory = null;
try { memory = await browser.eval('performance.measureUserAgentSpecificMemory().then(m=>({bytes:m.bytes,breakdown:m.breakdown.map(b=>({bytes:b.bytes,types:b.types}))}))'); } catch (e) { console.log('[memory]', e.message); }
let metrics = null;
try { await browser.send('Performance.enable'); metrics = await browser.send('Performance.getMetrics'); } catch {}
const packsBytes = (() => {
  const dir = path.join(ROOT, 'packs/gmod');
  let total = 0;
  for (const f of fs.readdirSync(dir)) total += fs.statSync(path.join(dir, f)).size;
  return total;
})();
const result = {
  ok: Boolean(markers.sandboxReady && (markers.prop || markers.map)),
  url: `http://127.0.0.1:${port}/runtime/player.html?game=gmod&map=gm_construct`,
  markers,
  index: index.gmod,
  packBytesOnDisk: packsBytes,
  memory,
  metrics: metrics ? Object.fromEntries(metrics.metrics.filter((m) => ['JSHeapUsedSize', 'JSHeapTotalSize'].includes(m.name)).map((m) => [m.name, m.value])) : null,
  seconds: Math.round((Date.now() - start) / 1000),
};
fs.writeFileSync(path.join(out, 'packed-player-console.log'), lines.join('\n'));
fs.writeFileSync(path.join(out, 'packed-player-result.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ ok: result.ok, seconds: result.seconds, markers, packBytesOnDisk: packsBytes, memory: memory?.bytes }, null, 2));

await browser.close();
server.kill('SIGTERM');
await new Promise((r) => (server.exitCode !== null ? r() : server.once('exit', r)));
if (!result.ok) process.exitCode = 1;
