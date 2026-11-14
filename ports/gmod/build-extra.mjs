#!/usr/bin/env node
// Builds the GMod runtime payload that rides next to the traced Source content pack:
//   packs/gmod/gmod-extra.bin   gzip chunk stream { u32 pathLen, u32 dataLen, path, data }[]
//   packs/gmod/gmod-extra.json  metadata (counts, curated addon list)
// and registers the gmod entry (runtime/gmod-host.js) in packs/index.json.
//
// Payload contents:
//   /gamebox-gmod/lua/bootstrap.lua, startup.lua (compat/sandbox.lua)
//   /gamebox-gmod/lua/**  the installed garrysmod/lua tree + gamemodes/{base,sandbox} Lua
//   /gmod-addons/<id>.gma real installed Workshop GMA archives (curated by size), parsed in
//                         the browser by runtime/gmod-host.js
//   /garrysmod/addons/<name>/**  real loose addons from garrysmod/addons
//
// Run after `node tools/pack.mjs --game gmod` (pack.mjs deletes unreferenced parts):
//   GAMEBOX_ENGINE=$PWD/ports/gmod/engine node ports/gmod/build-extra.mjs
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';

const ROOT = path.resolve(import.meta.dirname, '../..');
const compat = path.join(ROOT, 'ports/gmod/compat');
const install = process.env.GMOD_INSTALL || path.join(os.homedir(), '.local/share/Steam/steamapps/common/GarrysMod');
const gm = path.join(install, 'garrysmod');
const workshop = path.join(os.homedir(), '.local/share/Steam/steamapps/workshop/content/4000');
const outDir = path.join(ROOT, 'packs/gmod');
const MAX_GMA = Number(process.env.GMOD_MAX_GMA || 1.5 * 1024 * 1024);

if (!fs.existsSync(path.join(outDir, 'gmod-extra.bin')) && !fs.existsSync(path.join(ROOT, 'packs/index.json'))) {
  throw new Error('packs/gmod missing: run `node tools/pack.mjs --game gmod` first');
}
const indexFile = path.join(ROOT, 'packs/index.json');
const packs = JSON.parse(fs.readFileSync(indexFile, 'utf8'));
if (!packs.gmod?.data) throw new Error('packs/index.json has no gmod entry: run `node tools/pack.mjs --game gmod` first');

const entries = [];
function add(name, data) { entries.push({ name: '/' + name.replace(/^\/+/, ''), data }); }

// 1. Compatibility runtime sources.
add('gamebox-gmod/lua/bootstrap.lua', fs.readFileSync(path.join(compat, 'bootstrap.lua')));
add('gamebox-gmod/lua/startup.lua', fs.readFileSync(path.join(compat, 'sandbox.lua')));

// 2. Installed GMod Lua tree + the real gamemodes' Lua.
let luaFiles = 0;
function walk(dir, rel) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const abs = path.join(dir, e.name);
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) walk(abs, r);
    else if (e.isFile() && r.endsWith('.lua')) {
      add('gamebox-gmod/lua/' + r, fs.readFileSync(abs));
      luaFiles++;
    }
  }
}
for (const top of ['lua', 'gamemodes/base', 'gamemodes/sandbox']) {
  const dir = path.join(gm, top);
  if (fs.existsSync(dir)) walk(dir, top === 'lua' ? '' : top);
}

// 3. Curated real Workshop GMA archives (raw bytes; the host parses them in the browser).
const gmas = [];
for (const id of fs.existsSync(workshop) ? fs.readdirSync(workshop).sort() : []) {
  const dir = path.join(workshop, id);
  if (!fs.statSync(dir).isDirectory()) continue;
  const file = fs.readdirSync(dir).find((f) => f.endsWith('.gma'));
  if (!file) continue;
  const abs = path.join(dir, file);
  const size = fs.statSync(abs).size;
  if (size > MAX_GMA) continue;
  add(`gmod-addons/${id}.gma`, fs.readFileSync(abs));
  gmas.push({ id, name: file, size });
}

// 4. Real loose addons from garrysmod/addons.
const loose = [];
const looseRoot = path.join(gm, 'addons');
if (fs.existsSync(looseRoot)) {
  for (const name of fs.readdirSync(looseRoot).sort()) {
    const dir = path.join(looseRoot, name);
    if (!fs.statSync(dir).isDirectory()) continue;
    const files = [];
    const walkLoose = (d, rel) => {
      for (const e of fs.readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        const abs = path.join(d, e.name);
        const r = rel ? `${rel}/${e.name}` : e.name;
        if (e.isDirectory()) walkLoose(abs, r);
        else if (e.isFile()) {
          add(`garrysmod/addons/${name}/${r}`, fs.readFileSync(abs));
          files.push(r);
        }
      }
    };
    walkLoose(dir, '');
    if (files.length) loose.push({ id: name, files: files.filter((f) => f.endsWith('.lua')) });
  }
}

const raw = (() => {
  const chunks = [];
  let total = 0;
  for (const { name, data } of entries) {
    const nameBytes = Buffer.from(name);
    const head = Buffer.alloc(8);
    head.writeUInt32LE(nameBytes.length, 0);
    head.writeUInt32LE(data.length, 4);
    chunks.push(head, nameBytes, data);
    total += data.length;
  }
  return { buffer: Buffer.concat(chunks), total };
})();
const gz = zlib.gzipSync(raw.buffer, { level: 9 });
const hash = crypto.createHash('sha256').update(gz).digest('hex').slice(0, 16);
fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, 'gmod-extra.bin'), gz);

const meta = {
  version: 1,
  compression: 'gzip',
  layout: 'chunks',
  file: 'gmod-extra.bin',
  sha256: hash,
  bytes: gz.length,
  rawBytes: raw.total,
  entries: entries.length,
  luaFiles,
  gmas,
  loose,
  built: new Date().toISOString(),
};
fs.writeFileSync(path.join(outDir, 'gmod-extra.json'), JSON.stringify(meta, null, 1));

// 5. Register the runtime host and extra payload in this game's index key only.
packs.gmod.runtime = 'runtime/gmod-host.js';
packs.gmod.extra = 'gmod/gmod-extra.bin';
packs.gmod.extraBytes = gz.length;
fs.writeFileSync(indexFile, JSON.stringify(packs, null, 1));

const mb = (n) => (n / 1e6).toFixed(1) + ' MB';
console.log(`gmod-extra: ${entries.length} files (${luaFiles} GMod Lua), ${gmas.length} GMAs, ${loose.length} loose addons`);
console.log(`raw ${mb(raw.total)} -> ${mb(gz.length)} gzip (${path.join(outDir, 'gmod-extra.bin')})`);
console.log(`packs/index.json gmod.runtime = runtime/gmod-host.js, extra = ${packs.gmod.extra}`);
