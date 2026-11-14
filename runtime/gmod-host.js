// Runs packed Garry's Mod (packs/gmod) in this page: the Source engine from the trace pack plus
// the GMod compatibility runtime — GLua VM + Source plugin (libgamebox_gmod.so), the installed
// GMod Lua tree, the real sandbox gamemode and real mounted Workshop GMA / loose addons.
//
// The packed player is the Source client (HL2 game DLLs) rendering gm_construct; the server-side
// GMod layer is ports/gmod/compat's reimplementation. Client-realm Derma/VGUI is not implemented
// (see ports/gmod/REPORT.md). One engine per page: leaving means reloading.
//
//   await launchGame({ canvas, packs, manifest: 'gmod/manifest-….json', map: 'gm_construct' })
//
// The page must be cross-origin isolated (threads); see runtime/coi-sw.js.
import { attachSourceAchievements } from './activity.js';
import { partStore } from './engine-host.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchRetry(url, tries = 4) {
  for (let i = 0; ; i++) {
    try {
      const res = await fetch(url, { mode: 'cors', credentials: 'omit' });
      if (res.ok) return res;
      if (res.status === 404 || i >= tries - 1) throw new Error(`${res.status} for ${url}`);
    } catch (error) {
      if (i >= tries - 1) throw error;
    }
    await sleep(500 * 2 ** i);
  }
}

async function gunzip(buffer) {
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).arrayBuffer();
}

function sliceFile(manifest, file, parts) {
  const [, stream, offset, size] = file;
  const part = manifest.partSize;
  const out = new Uint8Array(size);
  let written = 0;
  for (let i = Math.floor(offset / part); written < size; i++) {
    const bytes = parts.get(`${stream}:${i}`);
    const start = Math.max(offset - i * part, 0);
    const end = Math.min(offset + size - i * part, bytes.length);
    out.set(bytes.subarray(start, end), written);
    written += end - start;
  }
  return out;
}

async function loadFiles(manifest, store, ids, onProgress) {
  const wanted = new Map();
  for (const id of ids) {
    const [, stream, offset, size] = manifest.files[id];
    for (let i = Math.floor(offset / manifest.partSize); i <= Math.floor((offset + Math.max(size, 1) - 1) / manifest.partSize); i++) {
      wanted.set(`${stream}:${i}`, manifest.streams[stream][i]);
    }
  }
  const parts = new Map();
  let done = 0;
  const queue = [...wanted];
  const next = async () => {
    while (queue.length) {
      const [key, hash] = queue.shift();
      parts.set(key, new Uint8Array(await store.get(hash)));
      onProgress?.(++done, wanted.size);
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, queue.length) }, next));
  return ids.map((id) => sliceFile(manifest, manifest.files[id], parts));
}

function splitArgs(args) {
  const params = [];
  const lines = [];
  let line = null;
  for (const arg of args) {
    if (arg.startsWith('+')) {
      line = [arg.slice(1)];
      lines.push(line);
    } else if (line) {
      line.push(/\s/.test(arg) && !/^".*"$/.test(arg) ? `"${arg}"` : arg);
    } else {
      params.push(arg);
    }
  }
  return { params, cfg: lines.length ? lines.map((l) => l.join(' ')).join('\n') + '\n' : '' };
}

// --- GMA reader (same format as ports/gmod/compat/addons.mjs, self-contained for the page) ---
const crcTable = Uint32Array.from({ length: 256 }, (_, n) => {
  for (let k = 0; k < 8; k++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) crc = crcTable[(crc ^ b) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
function addonPath(path) {
  if (typeof path !== 'string' || !path || path.includes('\\') || /[:\x00-\x1f]/.test(path) || path.startsWith('/') ||
      path.split('/').some((p) => !p || p === '.' || p === '..')) throw new Error(`Invalid addon path: ${path}`);
  return path.toLowerCase();
}
function readGma(input, { maxBytes = 256 * 1024 * 1024, maxFiles = 100000, strictArchiveCrc = true } = {}) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  if (bytes.length > maxBytes) throw new Error('GMA exceeds byte limit');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decode = new TextDecoder('utf-8', { fatal: true });
  const display = new TextDecoder();
  let pos = 0;
  const take = (n) => {
    if (!Number.isSafeInteger(n) || n < 0 || pos + n > bytes.length) throw new Error('GMA truncated/out of bounds');
    const start = pos; pos += n; return start;
  };
  const u32 = () => view.getUint32(take(4), true);
  const u64 = () => view.getBigUint64(take(8), true);
  const str = (strict = false) => {
    const start = pos;
    while (pos < bytes.length && bytes[pos] !== 0) pos++;
    if (pos === bytes.length) throw new Error('GMA truncated string');
    const out = (strict ? decode : display).decode(bytes.subarray(start, pos));
    pos++;
    return out;
  };
  if (decode.decode(bytes.subarray(take(4), 4)) !== 'GMAD') throw new Error('GMA magic mismatch');
  const version = bytes[take(1)];
  if (version < 1 || version > 3) throw new Error(`Unsupported GMA version ${version}`);
  u64(); u64();
  if (version > 1) while (str() !== '');
  const name = str(), description = str(), author = str();
  u32();
  const entries = [];
  const names = new Set();
  while (u32() !== 0) {
    if (entries.length >= maxFiles) throw new Error('GMA exceeds file limit');
    const path = addonPath(str(true));
    const size = u64();
    const crc = u32();
    if (names.has(path)) throw new Error(`GMA duplicate path: ${path}`);
    if (size > BigInt(maxBytes)) throw new Error('GMA entry out of bounds');
    names.add(path);
    entries.push({ path, size: Number(size), crc });
  }
  const files = new Map();
  for (const e of entries) {
    const start = take(e.size);
    const data = bytes.subarray(start, pos);
    if (e.crc !== 0 && crc32(data) !== e.crc) throw new Error(`GMA file CRC mismatch: ${e.path}`);
    files.set(e.path, data);
  }
  if (pos !== bytes.length && pos + 4 !== bytes.length) throw new Error('GMA trailing data out of bounds');
  let archiveCrcMismatch = false;
  if (pos + 4 === bytes.length) {
    const expected = crc32(bytes.subarray(0, pos));
    const supplied = u32();
    if (supplied !== 0 && expected !== supplied) {
      if (strictArchiveCrc) throw new Error('GMA archive CRC mismatch');
      archiveCrcMismatch = true;
    }
  }
  return { name, description, author, version, files, archiveCrcMismatch };
}

// --- GMod runtime payload (ports/gmod/build-extra.mjs) -------------------------------------
function unpackChunks(buffer) {
  const dv = new DataView(buffer);
  const td = new TextDecoder();
  const files = new Map();
  for (let o = 0; o < dv.byteLength;) {
    const pathLen = dv.getUint32(o, true);
    const dataLen = dv.getUint32(o + 4, true);
    const name = td.decode(new Uint8Array(buffer, o + 8, pathLen));
    files.set(name, new Uint8Array(buffer, o + 8 + pathLen, dataLen));
    o += 8 + pathLen + dataLen;
  }
  return files;
}

async function loadRuntimePayload(packs, game, onStatus) {
  const meta = await (await fetchRetry(new URL(`${game}/gmod-extra.json`, packs))).json();
  onStatus?.('Loading GMod runtime');
  const gz = new Uint8Array(await (await fetchRetry(new URL(`${game}/${meta.file}`, packs))).arrayBuffer());
  const files = unpackChunks(await gunzip(gz));
  // Base game realm-correct autorun order: all autorun/*.lua, then autorun/server/*.lua.
  const baseCommon = [], baseServer = [];
  for (const path of files.keys()) {
    const m = path.match(/^\/gamebox-gmod\/lua\/autorun\/([^/]+\.lua)$/);
    if (m) { baseCommon.push(m[1]); continue; }
    const s = path.match(/^\/gamebox-gmod\/lua\/autorun\/server\/([^/]+\.lua)$/);
    if (s) baseServer.push(s[1]);
  }
  baseCommon.sort(); baseServer.sort();

  // Real Workshop GMA archives: parsed here, in the page, from their actual bytes.
  const addonCommon = [], addonServer = [];
  const addons = [];
  for (const path of [...files.keys()]) {
    const m = path.match(/^\/gmod-addons\/(.+)\.gma$/);
    if (!m) continue;
    const id = m[1];
    const data = files.get(path);
    files.delete(path);
    let addon;
    try { addon = readGma(data); }
    catch (error) {
      if (!/archive CRC/.test(error.message)) { print('[gmod-host] GMA rejected', id, error.message); continue; }
      addon = readGma(data, { strictArchiveCrc: false });
      print('[gmod-host] GMA', id, 'has a wrong trailing archive CRC; per-file CRCs verified, mounting (tolerated)');
    }
    const common = [], server = [], client = [];
    for (const [name, bytes] of addon.files) {
      if (name.startsWith('lua/')) {
        const rel = name.slice(4);
        files.set('/gamebox-gmod/lua/' + rel, bytes);
        if (/^autorun\/[^/]+\.lua$/.test(rel)) common.push(rel);
        else if (/^autorun\/server\/[^/]+\.lua$/.test(rel)) server.push(rel);
        else if (/^autorun\/client\/[^/]+\.lua$/.test(rel)) client.push(rel);
      } else {
        files.set('/garrysmod/addons/' + id + '/' + name, bytes);
      }
    }
    for (const rel of common) addonCommon.push({ id, path: rel });
    for (const rel of server) addonServer.push({ id, path: rel });
    addons.push({ id, name: addon.name, kind: 'gma', files: addon.files.size, common: common.length, server: server.length, client: client.length });
  }
  // Real loose addons (garrysmod/addons/<name>): mount their Lua into the VM and list autorun.
  for (const addon of meta.loose || []) {
    const common = [], server = [], client = [];
    for (const rel of addon.files || []) {
      const data = files.get(`/garrysmod/addons/${addon.id}/${rel}`);
      if (!data) continue;
      files.set('/gamebox-gmod/lua/' + rel, data);
      if (/^autorun\/[^/]+\.lua$/.test(rel)) common.push(rel);
      else if (/^autorun\/server\/[^/]+\.lua$/.test(rel)) server.push(rel);
      else if (/^autorun\/client\/[^/]+\.lua$/.test(rel)) client.push(rel);
    }
    for (const rel of common) addonCommon.push({ id: addon.id, path: rel });
    for (const rel of server) addonServer.push({ id: addon.id, path: rel });
    addons.push({ id: addon.id, name: addon.id, kind: 'loose', common: common.length, server: server.length, client: client.length });
  }
  const byId = (a, b) => (a.id === b.id ? a.path.localeCompare(b.path) : a.id.localeCompare(b.id));
  const autorun = [...baseCommon, ...addonCommon.sort(byId).map((x) => x.path), ...baseServer, ...addonServer.sort(byId).map((x) => x.path)];
  files.set('/gamebox-gmod/lua/gamebox-addons.lua', new TextEncoder().encode('return {' + autorun.map((p) => JSON.stringify(p)).join(',') + '}'));
  return { meta, files, addons, autorun, authored: addons.reduce((a, x) => a + x.common + x.server, 0) };
}

export async function launchGame({
  canvas,
  packs,
  manifest: manifestRef,
  map,
  args = [],
  onProgress,
  onStatus,
  onMiss,
  onExit,
  onReady,
  onEngineUp,
  print = (...a) => console.log(...a),
}) {
  if (!crossOriginIsolated) throw new Error('This page is not cross-origin isolated, so the engine cannot use threads.');
  if (globalThis.Module?.gbStarted) throw new Error('An engine is already running in this page.');

  onStatus?.('Reading game index');
  const manifest = await (await fetchRetry(new URL(manifestRef, packs).href)).json();
  const store = partStore(packs, manifest.game);

  onStatus?.('Loading engine');
  const names = Object.keys(manifest.engine);
  const binaries = await loadFiles(manifest, store, names.map((n) => manifest.engine[n]), (d, t) => onProgress?.('Engine', d, t));
  const urls = {};
  let engineJs = '';
  names.forEach((name, i) => {
    if (name.endsWith('.js')) engineJs = new TextDecoder().decode(binaries[i]);
    else urls[name] = URL.createObjectURL(new Blob([binaries[i]], { type: name.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream' }));
  });
  const pluginIndex = names.indexOf('libgamebox_gmod.so');
  const plugin = pluginIndex >= 0 ? binaries[pluginIndex] : null;
  if (!plugin) throw new Error('pack is missing libgamebox_gmod.so (rebuild packs/gmod with ports/gmod/engine)');

  const runtime = await loadRuntimePayload(packs, manifest.game, onStatus);
  print(`[gmod-host] runtime payload: ${runtime.files.size} files, ${runtime.addons.length} addons ` +
    `(${runtime.addons.filter((a) => a.kind === 'gma').length} GMA), ${runtime.autorun.length} server autorun files`);

  // Settings ride in cfg/gamebox.cfg: the engine stops taking +commands after about two dozen.
  const { params, cfg } = splitArgs([
    ...args,
    '-insecure',
    '-gb_float_hdr',
    '+mat_disable_bloom', '1',
    '+plugin_load', 'bin/gamebox_gmod',
  ]);
  const gameMap = map || 'gm_construct';
  const fullArgs = [...params, ...(cfg ? ['+exec', 'gamebox'] : []), '+map', gameMap];
  const shared = { game: manifest.mod, args: fullArgs, manifest };
  const mount = () => {
    const FS = globalThis.FS;
    const write = (p, data) => {
      FS.mkdirTree(p.slice(0, p.lastIndexOf('/')));
      FS.writeFile(p, data);
    };
    write('/libgamebox_gmod.so', plugin);
    for (const [p, data] of runtime.files) write(p, data);
    if (cfg) write(`/${manifest.mod}/cfg/gamebox.cfg`, new TextEncoder().encode(cfg));
    print(`[gmod-host] mounted ${runtime.files.size} GMod files + libgamebox_gmod.so into the engine FS`);
  };
  globalThis.gbConfig = {
    ...shared,
    part: (hash) => store.get(hash),
    onProgress,
    onStatus,
    onMiss,
    onFatal: (message) => onExit?.({ error: message }),
    onQuit: () => onExit?.({ code: 0 }),
    onEngineUp: () => {
      // Sandbox key binds: Q spawn prop, G spawn NPC, F grab/drop, R freeze, C weld, Z undo, X remove.
      const bound = [['q', 'gb_spawn'], ['g', 'gb_spawnnpc'], ['f', 'gb_grab'], ['r', 'gb_freeze'], ['c', 'gb_weld'], ['z', 'gb_undo'], ['x', 'gb_remove']];
      const queue = bound.map(([key, cmd]) => `bind ${key} "cmd ${cmd}"`);
      const pump = setInterval(() => {
        while (queue.length && globalThis.Module?.gbCommand?.(queue[0])) queue.shift();
        if (!queue.length) { clearInterval(pump); print('[gmod-host] sandbox binds installed'); }
      }, 100);
      onEngineUp?.();
    },
  };
  const worker = new Blob([`self.gbConfig = ${JSON.stringify(shared)};\n`, engineJs], { type: 'text/javascript' });
  globalThis.Module = {
    gbStarted: true,
    canvas,
    mainScriptUrlOrBlob: worker,
    locateFile: (name) => urls[name] ?? name,
    print,
    printErr: (...a) => print('[err]', ...a),
    setStatus: () => {},
    onAbort: (what) => onExit?.({ error: String(what) }),
    onExit: (code) => onExit?.({ code }),
    onRuntimeInitialized: () => onReady?.(),
    preRun: [() => {
      attachSourceAchievements(globalThis.FS, manifest.game, manifest.mod);
      addEventListener('pagehide', () => globalThis.Module.gbPersist?.());
      mount();
    }],
  };

  const script = document.createElement('script');
  script.src = URL.createObjectURL(new Blob([engineJs], { type: 'text/javascript' }));
  document.body.append(script);

  return { manifest, store, runtime, persist: () => globalThis.Module.gbPersist?.() };
}
