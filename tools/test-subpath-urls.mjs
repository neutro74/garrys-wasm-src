// Transport regression: production modules mounted below a repository/ref AND extra directory.
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {launch} from './lib/browser.mjs';
const root=path.resolve('.'),prefix='/gh/demo/repo/ref/nested/',requests=[];
const server=http.createServer(async(req,res)=>{
 const pathname=new URL(req.url,'http://localhost').pathname;requests.push(pathname);
 res.setHeader('Cross-Origin-Opener-Policy','same-origin');res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
 if(!pathname.startsWith(prefix)){res.writeHead(404).end();return;}
 const name=pathname.slice(prefix.length);
 if(name==='test.html'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><body><canvas id="screen"></canvas></body>');return;}
 if(name==='out/subpath.bin'){res.end(Buffer.from([17,42,99]));return;}
 if(name==='out/subpath.wasm'){res.setHeader('Content-Type','application/wasm');res.end(Buffer.from([0,97,115,109,1,0,0,0]));return;}
 if(name==='asset/garrysmod/html/static.js'){res.setHeader('Content-Type','text/javascript');res.end('window.staticLoaded=true;');return;}
 if(name==='asset/mapimage/gm_construct'){res.setHeader('Content-Type','image/svg+xml');res.end('<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>');return;}
 if(!/^(web|tools|runtime|ports)\//.test(name)||name.split('/').includes('..')){res.writeHead(404).end();return;}
 try{const bytes=await fs.readFile(path.join(root,name));res.setHeader('Content-Type',/\.(mjs|js)$/.test(name)?'text/javascript':'application/octet-stream');res.end(bytes);}catch{res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
let browser;
try{
 browser=await launch();await browser.goto(origin+prefix+'test.html');
 const result=await browser.eval(`(async()=>{
  const platform=await import('./tools/lift/host/platform.mjs');
  const bytes=[...await platform.readBytes('out/subpath.bin')];
  const wasm=await platform.compileAsset('out/subpath.wasm');
  const {openStorage}=await import('./web/storage.js');
  const storage=await openStorage();const preferences=await storage.request('preferences');
  const {HtmlPanels}=await import('./web/html-panels.js');
  const panels=new HtmlPanels(document.body,document.querySelector('canvas'),()=>{});
  panels.handle({type:'create',id:1});
  panels.loadHtml(panels.panels.get(1), '<html><head><script src="static.js"><\\/script></head><body><script>const image=new Image(); image.src="asset://mapimage/gm_construct"; document.body.append(image);<\\/script></body></html>', 'asset://garrysmod/html/menu.html');
  const frame=panels.panels.get(1).frame;
  await new Promise((resolve,reject)=>{const deadline=Date.now()+5000;const timer=setInterval(()=>{if(frame.contentWindow.staticLoaded&&frame.contentDocument.images[0]?.complete){clearInterval(timer);resolve();}else if(Date.now()>deadline){clearInterval(timer);reject(Error('panel resources did not load'));}},25);});
  return {bytes,wasm:wasm instanceof WebAssembly.Module,preferences,base:frame.contentDocument.baseURI,static:frame.contentWindow.staticLoaded,dynamic:frame.contentDocument.images[0].src,extras:frame.contentDocument.querySelector('script[type=module]').src};
 })()`);
 assert.deepEqual(result,{bytes:[17,42,99],wasm:true,preferences:{saved:false},base:origin+prefix+'asset/garrysmod/html/',static:true,dynamic:origin+prefix+'asset/mapimage/gm_construct',extras:origin+prefix+'web/menu-extras.js'});
 for(const name of ['web/storage-worker.js','tools/lift/host/webfs.mjs','ports/gmod/compat/addons.mjs','out/subpath.bin','out/subpath.wasm','asset/garrysmod/html/static.js','asset/mapimage/gm_construct','web/menu-extras.js'])assert.ok(requests.includes(prefix+name),'missing request '+name);
 assert.deepEqual(requests.filter(p=>!p.startsWith(prefix)&&p!=='/favicon.ico'),[],'a transport request escaped the package prefix');
 console.log('PASS nested CDN prefix: binary/wasm fetch, storage worker imports/RPC, iframe preload and dynamic asset bridge, menu extras');
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
