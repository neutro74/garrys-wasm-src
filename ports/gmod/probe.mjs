// Diagnostic only: never executes game binaries or contacts Steam.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import os from 'node:os';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { launch } from '../../tools/lib/browser.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const game = path.join(os.homedir(), '.local/share/Steam/steamapps/common/GarrysMod');
const shots = path.join(here, 'shots');
fs.mkdirSync(shots, { recursive: true });
const names = ['garrysmod/bin/client.dll', 'garrysmod/bin/server.dll', 'garrysmod/bin/lua_shared.dll'];
const inventory = { bytes: 0, files: 0, formats: {}, components: {} };
function scan(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) scan(p);
    else if (entry.isFile()) {
      const size = fs.statSync(p).size;
      const fd = fs.openSync(p, 'r');
      const magic = Buffer.alloc(4); fs.readSync(fd, magic, 0, 4, 0); fs.closeSync(fd);
      const format = magic.equals(Buffer.from([127,69,76,70])) ? 'ELF' : magic.subarray(0,2).toString() === 'MZ' ? 'PE/MZ' : magic.equals(Buffer.from([0,97,115,109])) ? 'WASM' : 'other';
      inventory.bytes += size; inventory.files++;
      inventory.formats[format] = (inventory.formats[format] ?? 0) + 1;
    }
  }
}
scan(game);
for (const name of names) inventory.components[name] = { bytes: fs.statSync(path.join(game, name)).size, type: execFileSync('file', ['-b', path.join(game, name)], { encoding: 'utf8' }).trim() };
try {
  inventory.lua = execFileSync('luajit', ['-e', `dofile(${JSON.stringify(path.join(game, 'garrysmod/gamemodes/sandbox/gamemode/commands.lua'))})`], { encoding: 'utf8', stdio: ['ignore','pipe','pipe'] });
} catch (e) { inventory.lua = { exit: e.status, stderr: e.stderr.toString() }; }
fs.writeFileSync(path.join(here, 'inventory.json'), JSON.stringify(inventory, null, 2));

// This is a format/capability check, not a game launcher or a game performance test.
const html = `<!doctype html><meta charset="utf-8"><title>GMod feasibility diagnostic</title>
<style>body{background:#f4f1e7;color:#172b38;margin:42px;font:17px monospace}h1{font-size:25px}pre{white-space:pre-wrap;border-top:3px solid;padding-top:20px}small{color:#4b555b}</style>
<h1>GMod / browser feasibility diagnostic</h1><small>No game is running. Native format rejection is expected.</small><pre id="out">Checking…</pre>
<script type="module">
const result = { isolated: crossOriginIsolated, wasmControl: WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0])), binaries: [] };
for (const name of ${JSON.stringify(names)}) {
  const bytes = await (await fetch('/native?name='+encodeURIComponent(name))).arrayBuffer();
  let error; try { await WebAssembly.compile(bytes); } catch(e) { error = e.message; }
  result.binaries.push({ name, bytes: bytes.byteLength, validWasm: WebAssembly.validate(bytes), error });
}
result.gzip = await new Response((await fetch('/gzip')).body.pipeThrough(new DecompressionStream('gzip'))).text();
const cache = await caches.open('gmod-diagnostic-temporary');
await cache.put('/diagnostic-cache',new Response('ok'));result.cache = await (await cache.match('/diagnostic-cache')).text();await caches.delete('gmod-diagnostic-temporary');
const gl = document.createElement('canvas').getContext('webgl2');
result.webgl2 = !!gl;
if(gl){const ext=gl.getExtension('WEBGL_debug_renderer_info');result.gpu=ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);}
result.jsHeap = performance.memory ? { used:performance.memory.usedJSHeapSize,total:performance.memory.totalJSHeapSize,limit:performance.memory.jsHeapSizeLimit } : null;
document.getElementById('out').textContent = JSON.stringify(result,null,2);window.result=result;
</script>`;
const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  res.setHeader('Cross-Origin-Opener-Policy','same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
  if (url.pathname === '/') { res.setHeader('Content-Type','text/html');res.end(html); }
  else if (url.pathname === '/native' && names.includes(url.searchParams.get('name'))) { res.setHeader('Content-Type','application/octet-stream');fs.createReadStream(path.join(game,url.searchParams.get('name'))).pipe(res); }
  else if (url.pathname === '/gzip') res.end(gzipSync('gzip-ok'));
  else { res.statusCode=404;res.end(); }
});
const mem = execFileSync('free',['-m'],{encoding:'utf8'});
console.log(mem);
const available = Number(mem.split('\n').find(l=>l.startsWith('Mem:')).trim().split(/\s+/).at(-1));
if(available < 3000) throw new Error('Available RAM under 3000 MB; wait 60 seconds before retrying.');
for(const name of ['chromium','chromium-browser']) {
  try { const active=execFileSync('ps',['-C',name,'-o','pid='],{encoding:'utf8'}).trim();if(active) throw new Error('Existing Chromium PIDs: '+active); }
  catch(e){if(e.status!==1)throw e;}
}
await new Promise(r=>server.listen(0,'127.0.0.1',r));
let browser;
try {
  browser = await launch({gpu:true,profile:path.join(here,'.chromium-probe'),width:1280,height:1200});
  await browser.send('Performance.enable');
  await browser.goto('http://127.0.0.1:'+server.address().port+'/');
  let result;
  for(let i=0;i<120;i++){result=await browser.eval('window.result');if(result)break;await new Promise(r=>setTimeout(r,250));}
  if(!result)throw new Error('Diagnostic timed out');
  const metrics=await browser.send('Performance.getMetrics').catch(()=>null);
  result.cdpMetrics=metrics;
  const rows=execFileSync('ps',['-eo','pid=,ppid=,rss=,args='],{encoding:'utf8'}).trim().split('\n').map(line=>{
    const m=line.trim().match(/^(\d+)\s+(\d+)\s+(\d+)\s+(.*)$/);return m?{pid:+m[1],ppid:+m[2],rssKiB:+m[3],args:m[4]}:null;
  }).filter(Boolean);
  const root=rows.find(r=>r.args.includes('--user-data-dir='+path.join(here,'.chromium-probe')) && !r.args.includes('--type='));
  if(root){
    const ids=new Set([root.pid]);let size;
    do { size=ids.size;for(const r of rows)if(ids.has(r.ppid))ids.add(r.pid); }while(size!==ids.size);
    result.browserProcesses=rows.filter(r=>ids.has(r.pid)).map(({pid,rssKiB})=>({pid,rssKiB}));
    result.summedRssKiB=result.browserProcesses.reduce((a,r)=>a+r.rssKiB,0);
    try{result.summedPssKiB=[...ids].reduce((sum,pid)=>sum+Number(fs.readFileSync('/proc/'+pid+'/smaps_rollup','utf8').match(/^Pss:\s+(\d+)/m)[1]),0);}catch{result.summedPssKiB=null;}
  }
  fs.writeFileSync(path.join(shots,'probe.json'),JSON.stringify(result,null,2));
  await browser.screenshot(path.join(shots,'native-rejection.png'));
  console.log(JSON.stringify(result,null,2));
  if(!result.wasmControl || result.binaries.some(b=>b.validWasm || !b.error) || result.gzip!=='gzip-ok' || result.cache!=='ok')throw new Error('Unexpected diagnostic result');
} finally {
  if(browser)await browser.close();
  await new Promise(r=>server.close(r));
  fs.rmSync(path.join(here,'.chromium-probe'),{recursive:true,force:true,maxRetries:5,retryDelay:200});
}
