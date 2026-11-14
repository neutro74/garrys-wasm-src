#!/usr/bin/env node
// Headless check of the GPU backends: WebGPU first, WebGL2 fallback.
//
//   node tools/gpu-backends.mjs [--require-webgpu]
//
// Serves the repo over HTTP (ES modules need a real origin), runs
// runtime/gpu/test.html in Chromium with SwiftShader, and asserts that each
// backend clears to the requested colour and draws a triangle.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { launch } from './lib/browser.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json' };

function serve() {
  const server = http.createServer((req, res) => {
    const url = decodeURIComponent(req.url.split('?')[0]);
    const file = path.join(ROOT, url === '/' ? 'index.html' : url);
    if (!file.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    fs.readFile(file, (err, data) => {
      if (err) { res.writeHead(404).end(); return; }
      res.writeHead(200, { 'content-type': MIME[path.extname(file)] ?? 'application/octet-stream' });
      res.end(data);
    });
  });
  return new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server)));
}

const server = await serve();
const port = server.address().port;
let browser;
try {
  browser = await launch({ webgpu: true, width: 320, height: 240 });
  await browser.goto(`http://127.0.0.1:${port}/runtime/gpu/test.html`);
  let results;
  for (let i = 0; i < 100; i++) {
    results = await browser.eval('window.__gpuResults || null');
    if (results?.webgl2) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!results?.webgl2) throw new Error('GPU test page did not finish');
  console.log(JSON.stringify(results, null, 2));
  const requireWebgpu = process.argv.includes('--require-webgpu');
  const webgpu = !!results.webgpu?.ok;
  const webgl2 = !!results.webgl2?.ok;
  if (!webgl2) throw new Error('WebGL2 backend failed');
  if (requireWebgpu && !webgpu) throw new Error(`WebGPU backend failed: ${results.webgpu?.error ?? 'unknown'}`);
  console.log(webgpu ? 'PASS webgpu + webgl2' : 'PASS webgl2 (webgpu unavailable in this Chromium)');
} finally {
  await browser?.close();
  server.close();
}
