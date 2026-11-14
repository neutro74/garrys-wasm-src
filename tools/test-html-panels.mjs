import {spawn} from 'node:child_process';
import {launch} from './lib/browser.mjs';
const s=spawn('node',['web/server.mjs','--port','8674'],{stdio:'ignore'});let b;
try {await new Promise(r=>setTimeout(r,1500));b=await launch({width:1280,height:720,gpu:true,profile:'run/codex-html-profile'});await b.goto('http://127.0.0.1:8674/web/dev/panel-test.html');await new Promise(r=>setTimeout(r,3000));console.log(JSON.stringify(await b.eval('({base:__baseOf(), events:__events().filter(e=>e.type==="console").slice(0,5)})')));}finally{if(b)await b.close();s.kill();}
