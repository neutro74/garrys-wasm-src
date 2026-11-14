import {createFs} from '/tools/lift/host/webfs.mjs';
import {createOverlayRpc} from '/tools/lift/host/overlay-rpc.mjs';
self.onmessage=({data:m})=>{try{const fs=createFs({index:{},reader:()=>0,overlayRpc:createOverlayRpc(m.buffer)}).fs;
if(m.write){fs.mkdirSync('/overlay/garrysmod/save',{recursive:true});fs.writeFileSync('/overlay/garrysmod/save/regression.sav',new Uint8Array([17,42,99]));}
postMessage({bytes:[...fs.readFileSync('/overlay/garrysmod/save/regression.sav')],stat:fs.statSync('/overlay/garrysmod/save/regression.sav').size});}catch(e){postMessage({error:e.stack});}};
