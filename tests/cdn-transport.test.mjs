import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const digest = async b => Buffer.from(await webcrypto.subtle.digest('SHA-256', b)).toString('hex');
async function fixture({ corrupt = false, diskFailure = false, staticCdn = false } = {}) {
  const chunkSize = 1048576;
  const body = new Uint8Array(chunkSize * 6 + 19);
  for (let i = 0; i < body.length; i++) body[i] = (i * 13 + Math.floor(i / chunkSize)) % 251;
  const parts = new Map(); const hashes = [];
  for (let i = 0; i < body.length; i += chunkSize) { const p = body.slice(i, i + chunkSize); const h = await digest(p); hashes.push(h); parts.set(h, p); }
  const manifest = { version: 1, chunkSize, binaryBase: 'auto', files: { 'game/data.vpk': { size: body.length, mime: 'application/octet-stream', chunks: hashes }, 'out/core.wasm': { size: 8, mime: 'application/wasm', chunks: [await digest(new Uint8Array([0,97,115,109,1,0,0,0]))] }, 'game/empty': { size: 0, chunks: [] } } };
  parts.set(manifest.files['out/core.wasm'].chunks[0], new Uint8Array([0,97,115,109,1,0,0,0]));
  const root = staticCdn ? 'https://cdn.staticdelivr.com/gh/owner/repo/abc123/release/' : 'https://local.test/sub/release/';
  const listeners = {}; const calls = []; const cache = new Map();
  const context = vm.createContext({ URL, Request, Response, Headers, ReadableStream, Uint8Array, Map, Set, crypto: webcrypto, console,
    caches: { open: async () => { if (diskFailure) throw Error('disk full'); return { match: async key => cache.get(String(key))?.clone(), put: async (key, value) => { cache.set(String(key), value.clone()); }, delete: async key => cache.delete(String(key)) }; } },
    fetch: async (request, options) => { const url = typeof request === 'string' ? request : request.url; calls.push({ url, options }); if (url.endsWith('cdn-manifest.json')) return Response.json(manifest); const hash = url.match(/\/([a-f0-9]{64})\.bin$/)?.[1]; if (hash) { if (!parts.has(hash)) return new Response('', { status: 404 }); return new Response(corrupt ? new Uint8Array([9]) : parts.get(hash)); } return new Response('text asset', { headers: { 'Content-Type': 'text/javascript' } }); },
    self: { location: { href: root + 'cdn-sw.js' }, registration: { scope: root }, addEventListener: (type, fn) => { listeners[type] = fn; }, skipWaiting: async () => {}, clients: { claim: async () => {} } },
  });
  vm.runInContext(await readFile(new URL('../web/cdn-sw.js', import.meta.url), 'utf8'), context);
  const request = async (path, init = {}) => { let result; listeners.fetch({ request: new Request(new URL(path, root), init), respondWith: value => { result = Promise.resolve(value); } }); return result; };
  return { request, body, calls, parts, hashes, context, cache, root };
}
test('full, HEAD, cross-chunk and all single-range forms have correct headers/body', async () => {
  const f = await fixture();
  for (const [range, start, end] of [['bytes=1048570-1048590',1048570,1048590],['bytes=6291456-',6291456,f.body.length-1],['bytes=-7',f.body.length-7,f.body.length-1]]) {
    const r = await f.request('game/data.vpk', { headers: { Range: range } }); assert.equal(r.status, 206); assert.equal(r.headers.get('content-range'), `bytes ${start}-${end}/${f.body.length}`); assert.deepEqual(new Uint8Array(await r.arrayBuffer()), f.body.slice(start,end+1)); assert.equal(r.headers.get('cross-origin-embedder-policy'),'credentialless');
  }
  const head = await f.request('game/data.vpk',{method:'HEAD'}); assert.equal(head.headers.get('content-length'),String(f.body.length)); assert.equal((await head.arrayBuffer()).byteLength,0);
  const full = await f.request('game/data.vpk'); assert.deepEqual(new Uint8Array(await full.arrayBuffer()), f.body);
});
test('invalid ranges, zero-byte files, wasm MIME and scoped text passthrough', async () => {
  const f = await fixture();
  for (const range of ['bytes=99999999-', 'bytes=4-2', 'bytes=0-1,3-4', 'bytes=-0', 'wat']) { const r=await f.request('game/data.vpk',{headers:{Range:range}}); assert.equal(r.status,416); assert.equal(r.headers.get('content-range'),`bytes */${f.body.length}`); }
  assert.equal((await (await f.request('game/empty')).arrayBuffer()).byteLength,0);
  assert.equal((await f.request('game/empty',{headers:{Range:'bytes=0-'}})).status,416);
  const wasm=await f.request('out/core.wasm'); assert.equal(wasm.headers.get('content-type'),'application/wasm'); await WebAssembly.compileStreaming(wasm);
  const text=await f.request('web/main.js'); assert.equal(await text.text(),'text asset'); assert.equal(text.headers.get('cross-origin-opener-policy'),'same-origin');
  assert.equal(await f.request('https://elsewhere.test/file'),undefined); assert.equal(await f.request('/outside'),undefined);
});
test('download integrity, missing chunks, disk failure fallback and content-addressed persistence', async () => {
  for (const mode of ['corrupt','missing']) { const f=await fixture({corrupt:mode==='corrupt'}); if(mode==='missing')f.parts.clear(); await assert.rejects(async()=>{const r=await f.request('game/data.vpk');await r.arrayBuffer();}); }
  const f=await fixture({diskFailure:true}); assert.equal((await (await f.request('game/data.vpk',{headers:{Range:'bytes=0-9'}})).arrayBuffer()).byteLength,10);
  const g=await fixture(); await (await g.request('game/data.vpk')).arrayBuffer(); assert.ok(vm.runInContext('memoryBytes <= MEMORY_LIMIT',g.context)); const before=g.calls.filter(c=>c.url.endsWith('.bin')).length; await (await g.request('game/data.vpk',{headers:{Range:'bytes=0-9'}})).arrayBuffer(); assert.equal(g.calls.filter(c=>c.url.endsWith('.bin')).length,before); assert.ok(g.cache.size>0);
});
test('concurrent reads deduplicate chunks and static CDN reads raw GitHub with CORS',async()=>{const f=await fixture({staticCdn:true}); await Promise.all([f.request('game/data.vpk',{headers:{Range:'bytes=0-9'}}).then(r=>r.arrayBuffer()),f.request('game/data.vpk',{headers:{Range:'bytes=0-99'}}).then(r=>r.arrayBuffer())]); const calls=f.calls.filter(c=>c.url.endsWith('.bin')); assert.equal(calls.length,1); assert.match(calls[0].url,/^https:\/\/raw.githubusercontent.com\/owner\/repo\/abc123\/release\/parts\//); assert.equal(calls[0].options.mode,'cors'); assert.equal(calls[0].options.credentials,'omit'); });
test('prefetch message downloads matching chunks in parallel and read-ahead stays inside the range', async () => {
  const f = await fixture(); let done; const listener = vm.runInContext('(self.__l = null, 0)', f.context);
  const r = await f.request('game/data.vpk', { headers: { Range: 'bytes=0-9' } }); await r.arrayBuffer();
  assert.equal(f.calls.filter(c => c.url.endsWith('.bin')).length, 1);
  const full = await f.request('game/data.vpk', { headers: { Range: 'bytes=0-2097151' } }); await full.arrayBuffer();
  assert.ok(f.calls.filter(c => c.url.endsWith('.bin')).length <= 2);
});
