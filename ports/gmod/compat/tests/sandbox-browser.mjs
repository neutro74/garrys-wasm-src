// In-browser GMod sandbox test: loads gm_construct with the Source compatibility plugin,
// mounts real installed GMod Lua (modules, extensions, gamemodes) and real installed Workshop
// GMA archives (parsed in the page by compat/addons.mjs), runs their server autorun files,
// then drives the sandbox with console commands and screenshots the result.
//
//   scripts/heavy.sh node ports/gmod/compat/tests/sandbox-browser.mjs
//
// Evidence: ports/gmod/evidence/in-browser-sandbox-{console.log,result.json},
// screenshots in ports/gmod/shots/ and screenshots/gmod/.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';
import { spawn } from 'node:child_process';
import { launch } from '../../../../tools/lib/browser.mjs';
import { baseBundle } from '../../../../tools/lib/content.mjs';
import { GModContent } from '../content.mjs';

const here = path.resolve(import.meta.dirname, '..');
const root = path.resolve(here, '../../..');
const install = process.env.GMOD_INSTALL || path.join(os.homedir(), '.local/share/Steam/steamapps/common/GarrysMod');
const gm = path.join(install, 'garrysmod');
const workshop = path.join(os.homedir(), '.local/share/Steam/steamapps/workshop/content/4000');
const out = path.join(here, 'evidence');
const shots = path.join(here, '../shots');
const shotMirror = path.join(root, 'screenshots/gmod');
fs.mkdirSync(out, { recursive: true });
fs.mkdirSync(shots, { recursive: true });
fs.mkdirSync(shotMirror, { recursive: true });

const MAX_GMA = Number(process.env.GMOD_MAX_GMA || 1.5 * 1024 * 1024);
const MOUNT_ASSETS = process.env.GMOD_MOUNT_ASSETS === '1';
const NO_ADDONS = process.env.GMOD_NO_ADDONS === '1';
const map = process.env.GMOD_MAP || 'gm_construct';
const SESSION = `sandbox-${Date.now()}`;

// --- installed files the Lua layer needs ------------------------------------------------
const INSTALL_PATHS = [];
function collect(dir, base, filter = () => true) {
  if (!fs.existsSync(dir)) return;
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const abs = path.join(dir, e.name);
    const rel = base ? `${base}/${e.name}` : e.name;
    if (e.isDirectory()) collect(abs, rel, filter);
    else if (e.isFile() && filter(rel)) INSTALL_PATHS.push(rel);
  }
}
collect(path.join(gm, 'lua'), 'lua', (rel) => rel.endsWith('.lua'));
collect(path.join(gm, 'gamemodes/base'), 'gamemodes/base', (rel) => rel.endsWith('.lua'));
collect(path.join(gm, 'gamemodes/sandbox'), 'gamemodes/sandbox', (rel) => rel.endsWith('.lua'));
const installBundle = (() => {
  const chunks = [];
  for (const rel of INSTALL_PATHS) {
    const data = fs.readFileSync(path.join(gm, rel));
    const mount = rel.startsWith('lua/') ? rel.slice(4) : rel;
    const name = Buffer.from('/gamebox-gmod/lua/' + mount);
    const head = Buffer.alloc(8);
    head.writeUInt32LE(name.length, 0);
    head.writeUInt32LE(data.length, 4);
    chunks.push(head, name, data);
  }
  return Buffer.concat(chunks);
})();
console.log(`install bundle: ${INSTALL_PATHS.length} lua files, ${(installBundle.length / 1e6).toFixed(1)} MB`);

// Base game's own autorun. Realm order: autorun/*.lua everywhere, then autorun/server/*.lua.
function autorunLists(relRoot, prefix) {
  const rootDir = path.join(gm, relRoot);
  const common = [], server = [];
  if (!fs.existsSync(rootDir)) return { common, server };
  for (const name of fs.readdirSync(rootDir).sort()) {
    if (name.endsWith('.lua')) common.push(prefix + name);
  }
  const serverDir = path.join(rootDir, 'server');
  if (fs.existsSync(serverDir)) for (const name of fs.readdirSync(serverDir).sort()) {
    if (name.endsWith('.lua')) server.push(prefix + 'server/' + name);
  }
  return { common, server };
}
const baseAutorun = autorunLists('lua/autorun', 'autorun/');

// --- curated real Workshop GMA archives ------------------------------------------------
function gmaSize(file) {
  try { return fs.statSync(file).size; } catch { return Infinity; }
}
const gmas = [];
for (const id of fs.existsSync(workshop) ? fs.readdirSync(workshop).sort() : []) {
  const dir = path.join(workshop, id);
  if (!fs.statSync(dir).isDirectory()) continue;
  const file = fs.readdirSync(dir).find((f) => f.endsWith('.gma'));
  if (!file) continue;
  const abs = path.join(dir, file);
  const size = gmaSize(abs);
  if (size > MAX_GMA) continue;
  gmas.push({ id, name: file, size, file: abs });
}
console.log(`mounted GMA archives: ${gmas.length}, ${(gmas.reduce((a, g) => a + g.size, 0) / 1e6).toFixed(2)} MB total`);

// --- servers --------------------------------------------------------------------------
let enginePort;
const reserve = http.createServer();
await new Promise((r) => reserve.listen(0, '127.0.0.1', r));
enginePort = reserve.address().port;
await new Promise((r) => reserve.close(r));
const engine = spawn(process.execPath, ['tools/engine-server.mjs', '--port', String(enginePort)], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
  env: { ...process.env, GAMEBOX_ENGINE: process.env.GAMEBOX_ENGINE || '' },
});
engine.stderr.on('data', (d) => process.stderr.write('[engine-server] ' + d));
await new Promise((resolve, reject) => {
  engine.stdout.once('data', resolve);
  engine.once('error', reject);
  engine.once('exit', (code) => reject(new Error('engine server exited ' + code)));
});

let gmodContent;
const PRELOAD = `
preRun:[function(){
  addRunDependency('gmod-preload');
  import('/gmod/tests/preload.mjs').then(m=>m.mount(FS)).then(s=>{
    window.__gmodMount=s;console.log('[gmod-preload] mounted',s.addons.length,'GMAs,',s.luaFiles,'lua files');
    removeRunDependency('gmod-preload');
  }).catch(e=>{console.error('[gmod-preload] failed',e);Module.onAbort?.(String(e));});
}],`;

const onEngineUp = `
onEngineUp:()=>{
  const lines=(t)=>{try{Module.gbCommand(t)}catch(e){console.error('[gmod-driver]',e)}};
  const cmds=[];
  const send=(t)=>{cmds.push(t);lines(t)};
  window.__gbCommands=cmds;
  const at=(ms,fn)=>setTimeout(fn,ms);
  at(500,()=>{ ['q gb_spawn','f gb_grab','r gb_freeze','c gb_weld','z gb_undo','x gb_remove','g gb_spawnnpc'].forEach(([k,c])=>send('bind '+k+' "cmd '+c+'"')); send('gb_status'); });
  at(3000,()=>send('gb_hooks'));
  at(4500,()=>send('gb_spawn'));
  at(5000,()=>send('gb_spawn'));
  at(5500,()=>send('gb_spawn models/props_junk/wood_crate001a.mdl'));
  at(9000,()=>send('gb_status'));
  at(11000,()=>send('gb_hooks'));
  at(14000,()=>send('gb_spawnnpc'));
  at(18000,()=>send('gb_status'));
  at(21000,()=>send('gb_hooks'));
},`;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  try {
    // GMod installed-content routes for the engine FS hooks (same shape as tools/engine-server.mjs).
    if (url.pathname.startsWith('/t/gmod/')) {
      gmodContent ??= new GModContent();
      const parts = url.pathname.split('/').filter(Boolean);
      const op = parts[3];
      const rest = parts.slice(4).map(decodeURIComponent);
      if (op === 'index') { res.end(JSON.stringify(gmodContent.paths())); return; }
      if (op === 'base') { res.end(baseBundle(gmodContent)); return; }
      if (op === 'map') { res.end(); return; }
      if (op === 'file') { const data = gmodContent.read(rest.join('/')); res.writeHead(data ? 200 : 404); res.end(data ?? ''); return; }
      if (op === 'list') { res.end(gmodContent.glob(url.searchParams.get('pattern') || '').map((p) => '/' + p).join('\n')); return; }
      if (op === 'log' && req.method === 'POST') { res.end(); return; }
    }
    // Specific compatibility endpoints first (they live under /gmod/ but are generated).
    if (url.pathname === '/gmod/install-bundle.bin') {
      res.setHeader('Content-Type', 'application/octet-stream');
      res.end(installBundle);
      return;
    }
    if (url.pathname === '/gmod/gmas.json') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ gmas: gmas.map((g) => ({ id: g.id, size: g.size, url: `/gmod/gma/${g.id}.gma` })) }));
      return;
    }
    if (url.pathname.startsWith('/gmod/gma/')) {
      const id = url.pathname.slice('/gmod/gma/'.length).replace(/\.gma$/, '');
      const g = gmas.find((x) => x.id === id);
      if (!g) { res.writeHead(404); res.end(); return; }
      res.setHeader('Content-Type', 'application/octet-stream');
      res.end(fs.readFileSync(g.file));
      return;
    }
    if (url.pathname === '/gmod/autorun.json') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ base: baseAutorun }));
      return;
    }
    // Compatibility runtime files (bootstrap/sandbox/plugin/GMA parser).
    if (url.pathname.startsWith('/gmod/')) {
      if (url.pathname === '/gmod/tests/preload.mjs') {
        res.setHeader('Content-Type', 'text/javascript');
        res.end(preloadSource());
        return;
      }
      const file = path.resolve(here, decodeURIComponent(url.pathname.slice(6)));
      if (!file.startsWith(here + path.sep)) { res.writeHead(400); res.end('bad path'); return; }
      if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end('not found'); return; }
      res.setHeader('Content-Type', file.endsWith('.mjs') ? 'text/javascript' : 'application/octet-stream');
      res.end(fs.readFileSync(file));
      return;
    }
    // The harness, with our preload + command driver injected (tools/harness/index.html unmodified on disk).
    if (url.pathname === '/') {
      const html = fs.readFileSync(path.join(root, 'tools/harness/index.html'), 'utf8')
        .replace('window.Module = {', `window.Module = {${PRELOAD}`)
        .replace('window.gbConfig = {', `window.gbConfig = {${onEngineUp}`);
      res.setHeader('Content-Type', 'text/html');
      res.end(html);
      return;
    }
    // Everything else (engine static files, trace fallbacks) comes from tools/engine-server.mjs.
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const upstream = await fetch(`http://127.0.0.1:${enginePort}${req.url}`, {
      method: req.method,
      body: req.method === 'POST' ? Buffer.concat(chunks) : undefined,
    });
    res.writeHead(upstream.status, Object.fromEntries(upstream.headers));
    res.end(Buffer.from(await upstream.arrayBuffer()));
  } catch (error) {
    console.error('[sandbox-browser]', error);
    res.writeHead(500);
    res.end(String(error));
  }
});

// Served to the page as an ES module: mounts the plugin, GMod Lua and real GMA archives into
// the engine's virtual filesystem. GMA parsing runs here, in the browser, on the real bytes.
function preloadSource() {
  return `
import { readGma } from '/gmod/addons.mjs';
const MOUNT_ASSETS = ${MOUNT_ASSETS};const NO_ADDONS = ${NO_ADDONS};
export async function mount(FS) {
  const mkdir = (p) => FS.mkdirTree(p.slice(0, p.lastIndexOf('/')));
  const write = (p, data) => { mkdir(p); FS.writeFile(p, data); };
  const get = async (url) => new Uint8Array(await (await fetch(url)).arrayBuffer());
  const stats = { addons: [], luaFiles: 0, clientFiles: 0, files: 0, autorun: [], bytes: 0 };

  // Native Source compatibility plugin.
  write('/libgamebox_gmod.so', await get('/gmod/build/gamebox_gmod.so'));
  write('/gamebox-gmod/lua/bootstrap.lua', await get('/gmod/bootstrap.lua'));
  write('/gamebox-gmod/lua/startup.lua', await get('/gmod/sandbox.lua'));

  // Installed GMod Lua tree + gamemodes, one bundle.
  {
    const buf = await (await fetch('/gmod/install-bundle.bin')).arrayBuffer();
    const dv = new DataView(buf); const td = new TextDecoder();
    for (let o = 0; o < dv.byteLength;) {
      const pl = dv.getUint32(o, true), dl = dv.getUint32(o + 4, true);
      const name = td.decode(new Uint8Array(buf, o + 8, pl));
      write(name, new Uint8Array(buf, o + 8 + pl, dl));
      o += 8 + pl + dl;
    }
  }

  // Real Workshop GMA archives: parse in the page and mount.
  const { gmas } = await (await fetch('/gmod/gmas.json')).json();
  const { base } = await (await fetch('/gmod/autorun.json')).json();
  const addonCommon = [], addonServer = [];
  for (const g of gmas) {
    const bytes = await get(g.url);
    stats.bytes += bytes.length;
    let addon;
    try { addon = readGma(bytes); }
    catch (error) {
      if (!/archive CRC/.test(error.message)) { console.warn('[gmod-preload] GMA rejected', g.id, error.message); stats.rejected = (stats.rejected || []).concat({ id: g.id, error: error.message }); continue; }
      addon = readGma(bytes, { strictArchiveCrc: false });
      console.warn('[gmod-preload] GMA ' + g.id + ' has a wrong trailing archive CRC; per-file CRCs verified, mounting (tolerated)');
      stats.tolerated = (stats.tolerated || []).concat(g.id);
    }
    let lua = 0;
    for (const [name, data] of addon.files) {
      stats.files++;
      if (name.startsWith('lua/')) {
        lua++;
        stats.luaFiles++;
        write('/gamebox-gmod/lua/' + name.slice(4), data);
        if (/^lua\\/autorun\\/[^/]+\\.lua$/.test(name)) addonCommon.push({ id: g.id, path: 'autorun/' + name.slice('lua/autorun/'.length) });
        if (/^lua\\/autorun\\/client\\/[^/]+\\.lua$/.test(name)) stats.clientFiles++;
        if (/^lua\\/autorun\\/server\\/[^/]+\\.lua$/.test(name)) addonServer.push({ id: g.id, path: 'autorun/server/' + name.slice('lua/autorun/server/'.length) });
      } else if (MOUNT_ASSETS) {
        write('/garrysmod/addons/' + g.id + '/' + name, data);
      } else {
        stats.contentFiles = (stats.contentFiles || 0) + 1;
      }
    }
    stats.addons.push({ id: g.id, name: addon.name, files: addon.files.size, lua, bytes: bytes.length });
  }
  const byId = (a, b) => (a.id === b.id ? a.path.localeCompare(b.path) : a.id.localeCompare(b.id));
  const list = NO_ADDONS ? base.common.concat(base.server) : [...base.common, ...addonCommon.sort(byId).map((x) => x.path), ...base.server, ...addonServer.sort(byId).map((x) => x.path)];
  stats.autorun = list;
  write('/gamebox-gmod/lua/gamebox-addons.lua', 'return {' + list.map((p) => JSON.stringify(p)).join(',') + '}');
  console.log('[gmod-preload] autorun list: ' + list.length + ' server files (' + addonCommon.length + ' addon shared, ' + addonServer.length + ' addon server)');
  return stats;
}
`;
}

await new Promise((r) => server.listen(0, '127.0.0.1', r));
console.log(`sandbox test page: http://127.0.0.1:${server.address().port}/?game=gmod&map=${map}`);
const browser = await launch({ gpu: true, profile: path.join(here, '../tmp/chrome-sandbox-' + Date.now()) });
const lines = [];
browser.on('Runtime.consoleAPICalled', ({ args }) => {
  const line = args.map((a) => a.value ?? a.description ?? '').join(' ');
  lines.push(line);
  if (/\[gmod|\[gmod-|\[gmod-preload\]|LoadLibrary: path: libgamebox_gmod|plugin|PASS|ERROR|error/i.test(line)) console.log(line);
});
browser.on('Runtime.exceptionThrown', ({ exceptionDetails: e }) => {
  const line = '[exception] ' + (e.exception?.description ?? e.text);
  lines.push(line);
  console.log(line);
});

const args = encodeURIComponent('-insecure -developer 1 +plugin_load bin/gamebox_gmod');
await browser.goto(`http://127.0.0.1:${server.address().port}/?game=gmod&session=${SESSION}&map=${map}&args=${args}`);

const start = Date.now();
const markers = {};
let shotIndex = 0;
const shot = async (name) => {
  const file = path.join(shots, `${name}.png`);
  await browser.screenshot(file).catch((e) => console.log('[shot failed]', e.message));
  fs.copyFileSync(file, path.join(shotMirror, path.basename(file)));
  console.log('[shot]', file);
};
let lastShot = 0;
while (Date.now() - start < Number(process.env.GMOD_SECONDS || 150) * 1000) {
  await new Promise((r) => setTimeout(r, 1000));
  const text = lines.join('\n');
  for (const [name, re] of Object.entries({
    pluginLoaded: /\[gmod\] Source compatibility plugin loaded/,
    luaActivated: /\[gmod\] Lua server activated/,
    sandboxReady: /\[gmod-sandbox\] READY/,
    gamemode: /gamemode registered: sandbox/,
    addonLoad: /\[gmod-sandbox\] addon /,
    hookDispatch: /\[gmod-sandbox\] hook dispatch/,
    onEntityCreated: /hook dispatch OnEntityCreated/,
    propSpawn: /\[gmod-sandbox\] spawn|demo prop/,
    npcSpawn: /spawned NPC/,
    hookError: /HOOK ERROR/,
    mapLifecycle: /map lifecycle hooks dispatched/,
  })) if (!markers[name] && re.test(text)) { markers[name] = true; console.log('[marker]', name); }
  if (markers.sandboxReady && !markers.sandboxShot) {
    markers.sandboxShot = true;
    await shot(`sandbox-${shotIndex++}-ready`);
  } else if (markers.propSpawn && !markers.propsShot) {
    markers.propsShot = true;
    await new Promise((r) => setTimeout(r, 2500));
    await shot(`sandbox-${shotIndex++}-props`);
  } else if (markers.npcSpawn && !markers.npcShot) {
    markers.npcShot = true;
    await shot(`sandbox-${shotIndex++}-npc`);
  } else if (Date.now() - lastShot > 20000) {
    lastShot = Date.now();
    await shot(`sandbox-${shotIndex++}-t${Math.round((Date.now() - start) / 1000)}`);
  }
  const exit = lines.find((l) => l.includes('[gmod-test] PASS') || l.includes('[gmod-error]'));
  if (exit && exit.includes('[gmod-error]')) break;
  if (markers.propSpawn && markers.hookDispatch && markers.onEntityCreated && Date.now() - start > 40000) break;
}
const mount = await browser.eval('window.__gmodMount').catch(() => null);
const commands = await browser.eval('window.__gbCommands').catch(() => null);
let memory = null;
try { memory = await browser.eval('performance.measureUserAgentSpecificMemory().then(m=>({bytes:m.bytes,breakdown:m.breakdown.map(b=>({bytes:b.bytes,types:b.types}))}))'); } catch {}
let metrics = null;
try { await browser.send('Performance.enable'); metrics = await browser.send('Performance.getMetrics'); } catch {}
await shot(`sandbox-${shotIndex++}-final`);

fs.writeFileSync(path.join(out, 'in-browser-sandbox-console.log'), lines.join('\n'));
const result = {
  ok: Boolean(markers.sandboxReady && (markers.propSpawn || markers.npcSpawn) && markers.hookDispatch),
  map, session: SESSION, markers,
  pluginLoaded: Boolean(markers.pluginLoaded), sandboxReady: Boolean(markers.sandboxReady),
  addonMount: mount ? { addons: mount.addons, luaFiles: mount.luaFiles, files: mount.files, clientFiles: mount.clientFiles, autorun: mount.autorun } : null,
  commands,
  memory, metrics: metrics ? Object.fromEntries(metrics.metrics.filter((m) => ['JSHeapUsedSize', 'JSHeapTotalSize', 'TaskDuration'].includes(m.name)).map((m) => [m.name, m.value])) : null,
  seconds: Math.round((Date.now() - start) / 1000),
};
fs.writeFileSync(path.join(out, 'in-browser-sandbox-result.json'), JSON.stringify(result, null, 2));
console.log(JSON.stringify({ ok: result.ok, seconds: result.seconds, addons: result.addonMount?.addons.length, luaFiles: result.addonMount?.luaFiles, markers }, null, 2));

await browser.close();
await new Promise((r) => server.close(r));
engine.kill('SIGTERM');
await new Promise((r) => (engine.exitCode !== null ? r() : engine.once('exit', r)));
if (!result.ok) process.exitCode = 1;
