// Node probe: load real installed addon server autorun files on top of the sandbox startup
// (real GMod modules + gamemode + shims), then dispatch hooks. Iteration aid for the browser
// test; the browser test is the authoritative evidence.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { readGma } from './addons.mjs';
import { createRealm } from './host.mjs';

const install = process.env.GMOD_INSTALL || path.join(os.homedir(), '.local/share/Steam/steamapps/common/GarrysMod');
const gm = path.join(install, 'garrysmod');
const root = path.join(process.env.HOME, '.local/share/Steam/steamapps/workshop/content/4000');
const here = import.meta.dirname;

const base = new Map();
function walk(dir, rel) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, e.name);
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) walk(abs, r);
    else if (e.name.endsWith('.lua')) base.set(`lua/${r}`, fs.readFileSync(abs));
  }
}
walk(path.join(gm, 'lua'), '');
walk(path.join(gm, 'gamemodes'), 'gamemodes');
base.set('lua/startup.lua', fs.readFileSync(path.join(here, 'sandbox.lua')));

const prelude = fs.readFileSync(path.join(here, 'tests/sandbox-node.mjs'), 'utf8')
  .match(/const prelude = `([\s\S]*?)`;/)[1];

const ids = process.argv.slice(2);
const cases = [];
for (const id of (ids.length ? ids : fs.readdirSync(root).sort())) {
  const dir = path.join(root, id);
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) continue;
  const gma = fs.readdirSync(dir).find((f) => f.endsWith('.gma'));
  if (!gma) continue;
  const file = path.join(dir, gma);
  const size = fs.statSync(file).size;
  if (size > 256 * 1024 * 1024) continue;
  let addon;
  try { addon = readGma(fs.readFileSync(file)); } catch { continue; }
  cases.push({ id, name: addon.name, size, files: addon.files });
}

const report = [];
for (const c of cases) {
  const files = new Map(base);
  for (const [name, data] of c.files) files.set(name.startsWith('lua/') ? name : 'lua/' + name, data);
  const realm = await createRealm('server', { files, print: () => {}, printErr: () => {} });
  const result = { id: c.id, name: c.name, size: c.size, autoruns: [], hooks: [], thinkErrors: [] };
  try {
    realm.run(prelude, '@node-mocks');
    realm.include('startup.lua');
    const autoruns = [...c.files.keys()]
      .filter((p) => /^lua\/autorun\/[^/]+\.lua$/.test(p) || /^lua\/autorun\/server\/[^/]+\.lua$/.test(p))
      .sort();
    for (const p of autoruns) {
      try { realm.include(p.slice(4)); result.autoruns.push({ path: p, ok: true }); }
      catch (e) { result.autoruns.push({ path: p, ok: false, error: e.message.split('\n')[0] }); }
    }
    try {
      realm.run(`_G.__names={} for name,list in pairs(hook.GetTable()) do local owners={} for owner in pairs(list) do owners[#owners+1]=tostring(owner) end table.sort(owners) _G.__names[name]=owners end`);
      result.hooks = realm.run('local out={} for name,owners in pairs(__names) do out[#out+1]=name.." ["..table.concat(owners,",").."]" end table.sort(out) return table.concat(out,"; ")') ?? '';
    } catch {}
    for (let i = 0; i < 3; i++) {
      try {
        realm.setTime(i * 0.6);
        realm.run('GameBoxFrame()');
      } catch (e) { result.thinkErrors.push(e.message.split('\n')[0]); }
    }
  } catch (e) {
    result.startupError = e.message.split('\n')[0];
  } finally {
    realm.close();
  }
  report.push(result);
  const ok = result.autoruns.filter((a) => a.ok).length;
  console.log(`${c.id.padEnd(12)} ${(c.name || '').slice(0, 32).padEnd(34)} autorun ${ok}/${result.autoruns.length}` +
    (result.startupError ? ' STARTUP: ' + result.startupError : '') +
    result.autoruns.filter((a) => !a.ok).slice(0, 2).map((a) => `\n    ${a.path}: ${a.error}`).join('') +
    (result.thinkErrors.length ? `\n    think errors: ${result.thinkErrors.slice(0, 2).join(' | ')}` : ''));
}
fs.mkdirSync(path.join(here, 'evidence'), { recursive: true });
fs.writeFileSync(path.join(here, 'evidence/addon-sandbox-probe.json'), JSON.stringify(report, null, 2));
