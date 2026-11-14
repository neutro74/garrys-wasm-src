import {spawn} from 'node:child_process';
import fs from 'node:fs';
import {launch} from './lib/browser.mjs';
const s=spawn('node',['web/server.mjs','--port','8675'],{stdio:'ignore'});let b;
fs.mkdirSync('run/codex-interaction',{recursive:true});
try{
 await new Promise(r=>setTimeout(r,1500));b=await launch({width:1280,height:720,gpu:true,profile:'run/codex-interaction-profile'});
 b.on('Runtime.consoleAPICalled',p=>{const text=p.args.map(a=>a.value??a.description).join(' ');if(/audio|error|Error|FAILED|status|codex_input/.test(text))console.log(text);});
 await b.goto('http://127.0.0.1:8675/?cmd='+encodeURIComponent((process.env.TEST_NO_SOUND?' -nosound ':'')+'+map gm_construct'));
 await new Promise(r=>setTimeout(r,3000));
 await b.send('Input.dispatchMouseEvent',{type:'mousePressed',x:100,y:650,button:'left',clickCount:1});await b.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:100,y:650,button:'left',clickCount:1});
 const start=Date.now();while(Date.now()-start<240000){await new Promise(r=>setTimeout(r,2000));if(await b.eval('(window.__frames?.()??0)>200'))break;}
 await b.screenshot('run/codex-interaction/before.png');
 // A trusted input gesture starts audio, and the backquote opens the in-game console.
 const key=async(code,key,vk)=>{await b.send('Input.dispatchKeyEvent',{type:'keyDown',code,key,windowsVirtualKeyCode:vk});await b.send('Input.dispatchKeyEvent',{type:'keyUp',code,key,windowsVirtualKeyCode:vk});};
 await key('Backquote','`',192);await new Promise(r=>setTimeout(r,1500));
 await b.send('Input.dispatchMouseEvent',{type:'mousePressed',x:650,y:450,button:'left',clickCount:1});await b.send('Input.dispatchMouseEvent',{type:'mouseReleased',x:650,y:450,button:'left',clickCount:1});
 for(const c of 'echo codex_input_123'){const code=c===' '?'Space':/[a-z]/.test(c)?'Key'+c.toUpperCase():c==='_'?'Minus':'Digit'+c;await key(code,c,c===' '?32:c==='_'?189:c.toUpperCase().charCodeAt(0));}
 await key('Enter','Enter',13);await new Promise(r=>setTimeout(r,2000));
 await b.screenshot('run/codex-interaction/console.png');console.log('audio',JSON.stringify(await b.eval('window.__audio?.()')));
 await key('Backquote','`',192);await new Promise(r=>setTimeout(r,1000));
 await b.screenshot('run/codex-interaction/walk-before.png');
 await b.send('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyW',key:'w',windowsVirtualKeyCode:87});await new Promise(r=>setTimeout(r,2500));await b.send('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyW',key:'w',windowsVirtualKeyCode:87});
 await b.screenshot('run/codex-interaction/walk-after.png');console.log('audio-final',JSON.stringify(await b.eval('window.__audio?.()')));
}finally{if(b)await b.close();s.kill();}
