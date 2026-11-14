// Actual Chromium WASM checks; --engine adds the live Source/VPhysics test.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import {launch} from '../../../../tools/lib/browser.mjs';
import {baseBundle} from '../../../../tools/lib/content.mjs';
import {GModContent} from '../content.mjs';

const here=path.resolve(import.meta.dirname,'..'), root=path.resolve(here,'../../..');
const serveMode=process.argv.includes('--serve');
const engineMode=serveMode||process.argv.includes('--engine');
const gameArg=process.argv.indexOf('--game');
const game=gameArg<0?(serveMode?'gmod':'hl2'):process.argv[gameArg+1];
const mapArg=process.argv.indexOf('--map');
const map=mapArg<0?(game==='gmod'?'gm_construct':'d1_trainstation_02'):process.argv[mapArg+1];
let gmodContent;
const install=process.env.GMOD_INSTALL || path.join(process.env.HOME,'.local/share/Steam/steamapps/common/GarrysMod');
const out=path.join(here,'evidence');fs.mkdirSync(out,{recursive:true});
const core=new Map(['hook','list'].map(name=>[`/core/${name}.lua`,fs.readFileSync(path.join(install,`garrysmod/lua/includes/modules/${name}.lua`))]));
const startup=fs.readFileSync(path.join(here,serveMode?'preview.lua':'tests/source-physics.lua'));
const mounts=[['/libgamebox_gmod.so','/gmod/build/gamebox_gmod.so'],
  ['/gamebox-gmod/lua/bootstrap.lua','/gmod/bootstrap.lua'],
  ['/gamebox-gmod/lua/startup.lua','/startup.lua'],
  ['/gamebox-gmod/lua/includes/modules/hook.lua','/core/hook.lua']];
const preload=`preRun:[function(){addRunDependency('gmod-preload');(async()=>{
  for(const [name,url] of ${JSON.stringify(mounts)}){
    const response=await fetch(url);if(!response.ok)throw Error(url+': '+response.status);
    FS.mkdirTree(name.slice(0,name.lastIndexOf('/')));FS.writeFile(name,new Uint8Array(await response.arrayBuffer()));
  }
})().then(()=>removeRunDependency('gmod-preload')).catch(e=>{console.error(e);Module.onAbort?.(String(e));});}],`;
const standalone=`<!doctype html><meta charset="utf-8"><title>GLua browser test</title>
<pre id="output">Running actual GLua WASM tests…</pre><script type="module">
import {createRealm} from '/gmod/host.mjs';
try {
 const files=new Map();
 for(const name of ['hook','list'])files.set('lua/includes/modules/'+name+'.lua',new Uint8Array(await (await fetch('/core/'+name+'.lua')).arrayBuffer()));
 files.set('lua/autorun/test.lua','require("hook").Add("BrowserTest","test",function(x) return x*3 end)');
 const s=await createRealm('server',{files}), c=await createRealm('client',{files});
 const result=s.autorun();if(result.failed.length)throw Error(JSON.stringify(result));
 s.run('assert(SERVER && !CLIENT); assert(hook.Run("BrowserTest",7)==21); local x=0; for i=1,4 do if i==2 then continue end; x=x+i end; assert(x==8)');
 s.run('local list=require("list"); list.Set("Browser","value",42); assert(list.GetEntry("Browser","value")==42)');
 c.run('assert(CLIENT && !SERVER && hook==nil)');
 let limited=false;try{s.run('while true do end')}catch(e){limited=e.message.includes('budget')}
 if(!limited)throw Error('instruction limit failed');s.run('assert(true)');
 s.close();c.close();window.result={ok:true,realmIsolation:true,realHook:true,realList:true,continue:true,instructionLimit:true};
} catch(e){window.result={ok:false,error:e.stack}}
document.querySelector('#output').textContent=JSON.stringify(window.result,null,2);
</script>`;
let engine,enginePort,browser;
process.on('SIGINT',()=>{if(!serveMode){engine?.kill('SIGTERM');browser?.close().finally(()=>process.exit(130));}});
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  res.setHeader('Cross-Origin-Opener-Policy','same-origin');res.setHeader('Cross-Origin-Embedder-Policy','require-corp');
  try {
    if(core.has(url.pathname)){res.end(core.get(url.pathname));return;}
    if(url.pathname==='/startup.lua'){res.end(startup);return;}
    if(engineMode && game==='gmod' && url.pathname.startsWith('/t/gmod/')){
      gmodContent??=new GModContent();
      const [, , , , op,...rest]=url.pathname.split('/');
      if(op==='index'){res.end(JSON.stringify(gmodContent.paths()));return;}
      if(op==='base'){res.end(baseBundle(gmodContent));return;}
      if(op==='file'){
        const data=gmodContent.read(decodeURIComponent(rest.join('/')));
        res.writeHead(data?200:404);res.end(data??'');return;
      }
      if(op==='list'){res.end(gmodContent.glob(url.searchParams.get('pattern')||'').map(p=>'/'+p).join('\n'));return;}
      if(op==='map'){res.end();return;}
    }
    if(url.pathname.startsWith('/gmod/')){
      const file=path.resolve(here,decodeURIComponent(url.pathname.slice(6)));
      if(!file.startsWith(here+'/')){res.writeHead(400);res.end();return;}
      res.setHeader('Content-Type',file.endsWith('.mjs')?'text/javascript':file.endsWith('.wasm')?'application/wasm':'application/octet-stream');
      res.end(fs.readFileSync(file));return;
    }
    if(url.pathname==='/'){
      res.setHeader('Content-Type','text/html');
      let html=engineMode?fs.readFileSync(path.join(root,'tools/harness/index.html'),'utf8').replace('window.Module = {',`window.Module = {${preload}`):standalone;
      if(serveMode)html=html.replace("const game = q.get('game') || 'portal';","const game = q.get('game') || 'gmod';").replace("const args = [...(q.get('args') || '').split(' ').filter(Boolean)];",`const args = ['-insecure','-gb_float_hdr','+plugin_load','bin/gamebox_gmod',...(q.get('args')||'').split(' ').filter(Boolean)];`)
        .replace("if (q.get('map')) args.push('+map', q.get('map'));","args.push('+map',q.get('map')||'gm_construct');")
        .replace('window.gbConfig = {',`window.gbConfig = {onEngineUp:()=>{
          const commands=['bind q "cmd gb_spawn"','bind f "cmd gb_grab"','bind r "cmd gb_freeze"','bind c "cmd gb_weld"','bind z "cmd gb_undo"','bind x "cmd gb_remove"'];
          const install=setInterval(()=>{if(commands.length && Module.gbCommand?.(commands[0]))commands.shift();if(!commands.length)clearInterval(install);},100);
        },`);
      res.end(html);return;
    }
    if(engineMode){const response=await fetch(`http://127.0.0.1:${enginePort}${req.url}`,{method:req.method,...(req.method==='POST'?{body:await new Promise(r=>{let b='';req.on('data',c=>b+=c);req.on('end',()=>r(b));})}:{})});res.writeHead(response.status,Object.fromEntries(response.headers));res.end(Buffer.from(await response.arrayBuffer()));return;}
    res.writeHead(404);res.end();
  }catch(error){res.writeHead(500);res.end(String(error));}
});
const lines=[];
try {
  if(engineMode){
    const reserve=http.createServer();await new Promise(r=>reserve.listen(0,'127.0.0.1',r));enginePort=reserve.address().port;await new Promise(r=>reserve.close(r));
    engine=spawn(process.execPath,['tools/engine-server.mjs','--port',String(enginePort)],{cwd:root,stdio:['ignore','pipe','pipe']});
    await new Promise((resolve,reject)=>{engine.stdout.once('data',resolve);engine.once('error',reject);engine.once('exit',code=>reject(Error('engine server exited '+code)));});
  }
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  if(serveMode){
    console.log(`GMod compatibility development preview: http://127.0.0.1:${server.address().port}/`);
    console.log('Q spawn | F grab/drop | R freeze | C select/weld | Z undo | X remove. Ctrl+C stops both local servers.');
    await new Promise(resolve=>{process.once('SIGINT',resolve);process.once('SIGTERM',resolve);});
  }else{
  browser=await launch({gpu:engineMode});
  browser.on('Runtime.consoleAPICalled',({args})=>{const line=args.map(a=>a.value??a.description??'').join(' ');lines.push(line);if(line.includes('[gmod')||line.includes('[abort]'))console.log(line);});
  browser.on('Runtime.exceptionThrown',({exceptionDetails:e})=>{lines.push('[exception] '+(e.exception?.description??e.text));});
  const query=engineMode?`?game=${game}&session=gmod-plugin-test&map=${map}&args=`+encodeURIComponent('-insecure -gb_float_hdr +developer 1 +log on +plugin_load bin/gamebox_gmod'):'';
  await browser.goto(`http://127.0.0.1:${server.address().port}/${query}`);
  const deadline=Date.now()+(engineMode?120000:20000);let result;
  while(Date.now()<deadline){
    if(engineMode){
      const error=lines.find(l=>l.includes('[gmod-error]'));
      if(error){result={ok:false,error};break;}
      if(lines.some(l=>l.includes('[gmod-test] PASS'))){result={ok:true,liveSourcePhysics:true};break;}
      if(lines.some(l=>l.includes('[gmod-test] SPAWN'))&&!fs.existsSync(path.join(out,'source-spawn.png')))await browser.screenshot(path.join(out,'source-spawn.png'));
    }else {result=await browser.eval('window.result');if(result)break;}
    await new Promise(r=>setTimeout(r,250));
  }
  await browser.screenshot(path.join(out,engineMode?'source-result.png':'runtime-result.png'));
  fs.writeFileSync(path.join(out,engineMode?'source-console.log':'runtime-console.log'),lines.join('\n'));
  fs.writeFileSync(path.join(out,engineMode?'source-result.json':'runtime-result.json'),JSON.stringify(result??{ok:false,error:'timeout'},null,2));
  assert.equal(result?.ok,true,JSON.stringify(result??{error:'timeout; inspect evidence console'}));
  console.log(JSON.stringify(result));
  }
}finally{
  if(browser)await browser.close();
  if(server.listening)await new Promise(r=>server.close(r));
  if(engine){engine.kill('SIGTERM');await new Promise(r=>engine.exitCode!==null?r():engine.once('exit',r));}
}
