// Functional end-to-end check of the packaged build; not a controlled FPS benchmark.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {launch} from './lib/browser.mjs';
const bundle=path.resolve(process.argv[2]??'dist/gmod-web'),port=8500+Math.floor(Math.random()*300);
assert.ok(fs.existsSync(path.join(bundle,'performance-build.json')),'missing optimized package inventory');
const server=spawn('node',['web/server.mjs','--static',bundle,'--port',String(port)],{stdio:'ignore'});
let browser;const errors=[];
try{
 await new Promise(r=>setTimeout(r,1000));
 browser=await launch({gpu:true,headed:true,profile:path.resolve('run/static-n4000-profile')});
 browser.on('Runtime.exceptionThrown',p=>errors.push(p.exceptionDetails.exception?.description??p.exceptionDetails.text));
 browser.on('Runtime.consoleAPICalled',p=>{const line=p.args.map(a=>a.value??a.description).join(' ');if(line.includes('[render]'))errors.push(line);});
 await browser.goto(`http://127.0.0.1:${port}/?low=1&cmd=${encodeURIComponent('+fps_max_nofocus 0 +map gm_construct')}`);
 let frames=0;
 for(let elapsed=0;elapsed<180&&frames<900;elapsed+=5){await new Promise(r=>setTimeout(r,5000));frames=await browser.eval('window.__frames?.()??0');if(elapsed%15===0)console.log('[static] loaded frames',frames);}
 assert.ok(frames>=900,'packaged game did not reach steady map gameplay');
 await browser.screenshot('run/n4000-static-before.png');
 await browser.send('Input.dispatchMouseEvent',{type:'mousePressed',x:600,y:300,button:'left',clickCount:1});
 await browser.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:600,y:300,button:'left',clickCount:1});
 await browser.send('Input.dispatchKeyEvent',{type:'keyDown',key:'w',code:'KeyW',windowsVirtualKeyCode:87,nativeVirtualKeyCode:87});
 await new Promise(r=>setTimeout(r,1500));
 await browser.send('Input.dispatchKeyEvent',{type:'keyUp',key:'w',code:'KeyW',windowsVirtualKeyCode:87,nativeVirtualKeyCode:87});
 await new Promise(r=>setTimeout(r,1000));
 await browser.screenshot('run/n4000-static-after.png');
 let menu=null;
 for(let attempt=0;attempt<2;attempt++){
  const locked=await browser.eval('!!document.pointerLockElement');
  await browser.send('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
  await new Promise(r=>setTimeout(r,100));
  await browser.send('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
  await new Promise(r=>setTimeout(r,1000));
  menu=await browser.eval(`(()=>{const frame=[...document.querySelectorAll('iframe')].find(f=>f.contentDocument?.body?.getAttribute('ng-app')==='MenuApp');if(!frame)return null;return {bootstrapped:!!frame.contentWindow.angular?.element(frame.contentDocument.body).injector(),visible:getComputedStyle(frame).display!=='none'};})()`);
  console.log('[static] Escape',attempt+1,'pointer locked before',locked,'after',await browser.eval('!!document.pointerLockElement'),'menu',menu);
  if(menu?.visible)break;
 }
 assert.deepEqual(menu,{bootstrapped:true,visible:true},'the original menu must bootstrap and open after Escape');
 await browser.screenshot('run/n4000-static-menu.png');
 console.log('PASS original Angular menu bootstrap and Escape navigation');
 const after=await browser.eval('window.__frames?.()??0'),gpu=await browser.eval('window.__gpu?.()??null');
 assert.ok(after>frames+10,'game stopped presenting after input');assert.equal(gpu?.stats.shaderErrors,0);assert.deepEqual(errors,[]);
 console.log('PASS packaged gm_construct, real input delivery, continued rendering and zero renderer/shader errors');
 console.log('[static] functional smoke only; no FPS claim');
}finally{if(browser)await browser.close();server.kill();}
