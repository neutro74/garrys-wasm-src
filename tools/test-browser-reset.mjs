import assert from 'node:assert/strict';import {launch} from './lib/browser.mjs';import {spawn} from 'node:child_process';
const server=spawn('node',['web/server.mjs','--port','8692'],{stdio:'ignore'});let b;
try{await new Promise(r=>setTimeout(r,800));b=await launch({gpu:true});await b.goto('http://127.0.0.1:8692/web/dev/reset-test.html');let result;for(let i=0;i<100;i++){await new Promise(r=>setTimeout(r,100));result=await b.eval('window.result');if(result&&!result.pending)break;}assert.deepEqual(result,{failures:[]});console.log('PASS same-size backbuffer recreation and OffscreenCanvas presentation');
const uploads=await b.eval(`(()=>{
 const gl=new OffscreenCanvas(8,8).getContext('webgl2');
 const shared=new Uint8Array(new SharedArrayBuffer(16));shared.set([3,5,7,9],4);
 const buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);
 gl.bufferData(gl.ARRAY_BUFFER,16,gl.DYNAMIC_DRAW);gl.bufferSubData(gl.ARRAY_BUFFER,0,shared.subarray(4,8));
 shared.fill(0);const out=new Uint8Array(4);gl.getBufferSubData(gl.ARRAY_BUFFER,0,out);
 shared.set([11,13,17,255],4);const tex=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,tex);
 gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,shared.subarray(4,8));
 shared.fill(0);const fb=gl.createFramebuffer();gl.bindFramebuffer(gl.FRAMEBUFFER,fb);
 gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,tex,0);
 const pixel=new Uint8Array(4);gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
 return {buffer:[...out],pixel:[...pixel],error:gl.getError()};
})()`);
assert.deepEqual(uploads,{buffer:[3,5,7,9],pixel:[11,13,17,255],error:0});
console.log('PASS actual WebGL shared upload views, offsets and synchronous snapshot consumption');}finally{if(b)await b.close();server.kill();}
