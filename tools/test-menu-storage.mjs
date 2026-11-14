#!/usr/bin/env node
// End-to-end check of addon install and saves through GMod's own menu pages (web/menu-extras.js), in Chromium:
//   1. main menu -> Addons -> "Install addon from file…" with a generated .gma (file chooser answered over CDP)
//   2. start gm_construct; the addon's client and server autorun files must run (they write data/ files) and the
//      client then saves with gm_save; the .gms must reach OPFS
//   3. Saves page -> "Export saves" downloads a .gwb; "Import saves…" reads it back
//   node tools/test-menu-storage.mjs [--headed] [--profile run/menu-storage-profile] [--work run/menu-storage]
//   (both directories are recreated; give concurrent runs their own)
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './lib/browser.mjs';
import { writeGma } from '../ports/gmod/compat/addons.mjs';

const arg = (n, d) => { const i = process.argv.indexOf('--' + n); return i > 0 ? process.argv[i + 1] : d; };
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const work = path.resolve(arg('work', 'run/menu-storage')); fs.rmSync(work, { recursive: true, force: true }); fs.mkdirSync(work, { recursive: true });
const profile = path.resolve(arg('profile', 'run/menu-storage-profile')); fs.rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });

const MARK = 'wasm_addon_proof';
const gmaFile = path.join(work, 'wasm_proof.gma');
fs.writeFileSync(gmaFile, writeGma({
  name: 'WASM Proof Addon',
  files: new Map([
    ['lua/autorun/client/wasm_proof_cl.lua', `file.Write("${MARK}_client.txt", "client " .. os.time())
timer.Simple(15, function() RunConsoleCommand("gm_save") end)`],
    ['lua/autorun/server/wasm_proof_sv.lua', `file.Write("${MARK}_server.txt", "server " .. os.time())`],
  ]),
}));

const port = 8100 + Math.floor(Math.random() * 500);
const server = spawn('node', [path.join(repo, 'web/server.mjs'), '--port', String(port)], { stdio: ['ignore', 'ignore', 'inherit'] });
await new Promise((r) => setTimeout(r, 1500));
const browser = await launch({ width: 1280, height: 720, gpu: process.env.WEB_GPU === '1', profile, headed: process.argv.includes('--headed') });
const fail = async (msg) => { console.log(`FAIL ${msg}`); await browser.close().catch(() => {}); server.kill(); process.exit(1); };
browser.on('Runtime.exceptionThrown', (p) => console.log(`[page] exception: ${p.exceptionDetails.exception?.description ?? p.exceptionDetails.text}`));
await browser.send('Page.setInterceptFileChooserDialog', { enabled: true });
await browser.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: work, eventsEnabled: true }).catch(() => {});
let chooser = null;
browser.on('Page.fileChooserOpened', (p) => { chooser = p; });
// TEST_LOG=regex prints matching page console lines (with GUEST_LOG_FILES=1 to see the engine's file operations).
if (process.env.TEST_LOG) browser.on('Runtime.consoleAPICalled', (p) => { const t = p.args.map((a) => a.value ?? a.description).join(' '); if (new RegExp(process.env.TEST_LOG).test(t)) console.log(`[page] ${t}`); });
await browser.goto(`http://127.0.0.1:${port}/${process.env.TEST_LOG ? '?GUEST_LOG_FILES=1' : '?edition=base'}`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (what, expr, seconds) => {
  for (let t = 0; t < seconds; t += 2) { const v = await browser.eval(expr).catch(() => null); if (v) return v; await sleep(2000); }
  const diag = await browser.eval(`(()=>{const w=${MENU};if(!w)return 'no menu frame';const d=w.document;return JSON.stringify({hash:w.location.hash,host:!!w.__gmodHost,extras:[...d.scripts].some(s=>/menu-extras/.test(s.src)),ctrls:[...d.querySelectorAll('[ng-controller]')].map(e=>e.getAttribute('ng-controller')),options:d.querySelectorAll('.options > ul').length,notes:[...d.querySelectorAll('.wasm-note')].map(n=>n.textContent)})})()`).catch((e) => e.message);
  console.log(`diagnostics: ${diag}`);
  return fail(`timed out waiting for ${what}`);
};
// The menu page is the panel iframe running Angular with the main menu controller.
const MENU = `[...document.querySelectorAll('iframe')].map(f=>{try{return f.contentWindow}catch{return null}}).find(w=>w?.angular&&w.document.querySelector('[ng-view],.ng-scope'))`;
const gesture = (expression) => browser.send('Runtime.evaluate', { expression, userGesture: true, awaitPromise: true, returnByValue: true });
const opfs = (rel) => `(async()=>{try{let d=await (await navigator.storage.getDirectory()).getDirectoryHandle('garrys-wasm');const parts=${JSON.stringify(rel)}.split('/');const last=parts.pop();for(const p of parts)d=await d.getDirectoryHandle(p);if(last==='*'){const out=[];for await(const [n] of d.entries())out.push(n);return out.join(',')}await d.getFileHandle(last);return true}catch{return false}})()`;
const pick = async (expr, file) => {
  chooser = null;
  await gesture(expr);
  for (let i = 0; i < 20 && !chooser; i++) await sleep(250);
  if (!chooser) return fail('file chooser did not open');
  await browser.send('DOM.setFileInputFiles', { files: [file], backendNodeId: chooser.backendNodeId });
};

await until('main menu', `!!(${MENU})`, 240);
console.log('main menu up');
await browser.eval(`(${MENU}).location.hash = '#/addons/'`);
await until('Install addon link', `[...(${MENU}).document.querySelectorAll('a')].some(a=>a.textContent.startsWith('Install addon from file'))`, 30);
await pick(`[...(${MENU}).document.querySelectorAll('a')].find(a=>a.textContent.startsWith('Install addon from file')).click()`, gmaFile);
const installed = await until('install status', `(()=>{const t=[...(${MENU}).document.querySelectorAll('.wasm-note')].map(n=>n.textContent).join('|');return /Installed|Could not/.test(t)&&t})()`, 30);
console.log(`addons page: ${installed}`);
if (!/Installed/.test(installed)) await fail('addon not installed');
await until('addon listed', `[...(${MENU}).document.querySelectorAll('a')].some(a=>a.textContent.startsWith('WASM Proof Addon'))`, 20);
if (!await browser.eval(opfs('overlay/garrysmod/addons/wasm-proof.gma'))) {
  console.log(await browser.eval(`(async()=>{const out=[];const walk=async(d,p)=>{for await(const [n,h] of d.entries()){out.push(p+n);if(h.kind==='directory'&&out.length<200)await walk(h,p+n+'/');}};await walk(await navigator.storage.getDirectory(),'');return out.join(' ')})()`));
  await fail('addon not in OPFS');
}
console.log('PASS addon installed from the in-game Addons page, listed and persisted');

await browser.eval(`(${MENU}).lua.Run('RunConsoleCommand("map", "gm_construct")')`);
console.log('map requested');
// Frames plus the renderer's GPU resource totals (MB and live WebGL objects), to catch leaks.
const frames0 = Date.now(); const progress = setInterval(async () => {
  const g = await browser.eval('(()=>{const g=window.__gpu?.();return g&&{t:g.texture>>20,s:g.surface>>20,b:g.buffer>>20,live:g.live}})()').catch(() => null);
  console.log(`t=${((Date.now() - frames0) / 1000).toFixed(0)}s frames=${await browser.eval('window.__frames?.()').catch(() => '?')} gpu=${JSON.stringify(g)}`);
}, 10000);
await until('server autorun', opfs(`overlay/garrysmod/data/${MARK}_server.txt`), 300);
console.log('PASS addon server autorun ran (data file persisted)');
await until('client autorun', opfs(`overlay/garrysmod/data/${MARK}_client.txt`), 120);
console.log('PASS addon client autorun ran (data file persisted)');
const saves = await until('gm_save', `(async()=>{const s=await ${opfs('overlay/garrysmod/saves/*')};return /\\.gms/.test(s)&&s})()`, 120);
console.log(`PASS gm_save wrote ${saves}`);

await browser.eval(`(${MENU}).lua.Run('gui.ActivateGameUI()')`);
await browser.eval(`(${MENU}).location.hash = '#/saves/local/'`);
await until('Export saves link', `[...(${MENU}).document.querySelectorAll('a')].some(a=>a.textContent==='Export saves')`, 30);
await gesture(`[...(${MENU}).document.querySelectorAll('a')].find(a=>a.textContent==='Export saves').click()`);
const exported = path.join(work, 'gmod-saves.gwb');
for (let i = 0; i < 30 && !fs.existsSync(exported); i++) await sleep(500);
if (!fs.existsSync(exported)) await fail('export did not download');
console.log(`PASS saves exported from the in-game Saves page (${fs.statSync(exported).size} bytes)`);
await pick(`[...(${MENU}).document.querySelectorAll('a')].find(a=>a.textContent==='Import saves…').click()`, exported);
const imported = await until('import status', `(()=>{const t=[...(${MENU}).document.querySelectorAll('.wasm-note')].map(n=>n.textContent).join('|');return /Imported|nvalid|outside/.test(t)&&t})()`, 30);
if (!/Imported [1-9]/.test(imported)) await fail(`import: ${imported}`);
console.log(`PASS saves imported from the in-game Saves page (${imported.match(/Imported [^.|]*/)[0]})`);
await browser.screenshot(path.join(work, 'saves-page.png'));
clearInterval(progress);
console.log('ALL PASS');
await browser.close();
server.kill();
process.exit(0);
