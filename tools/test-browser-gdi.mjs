import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { launch } from './lib/browser.mjs';
const server = spawn('node', ['web/server.mjs', '--port', '8673'], {stdio:'ignore'});
let browser;
try {
  await new Promise(r => setTimeout(r, 1500));
  browser = await launch({width:640,height:480,gpu:true,profile:'run/codex-gdi-profile'});
  await browser.goto('http://127.0.0.1:8673/web/dev/panel-test.html');
  const result = await browser.eval(`(async () => {
    const {initGdi, installGdi} = await import('/tools/lift/host/gdi.mjs');
    await initGdi();
    const SHIMS = {}; installGdi(SHIMS, {wstr:()=>''});
    const bytes = new Uint8Array(1024*1024), dv = new DataView(bytes.buffer); let next=1024;
    const g = {u8:bytes,dv,alloc(n){const p=next;next=(next+n+7)&~7;return p;},
      u32(p,v){if(v!==undefined)dv.setUint32(p,v,true);return dv.getUint32(p,true);},
      i32(p){return dv.getInt32(p,true);},
      readStr(p){let s='';while(bytes[p])s+=String.fromCharCode(bytes[p++]);return s;},
      cstr(s){const p=this.alloc(s.length+1);bytes.set(new TextEncoder().encode(s),p);return p;}};
    const call=(n,...a)=>SHIMS['gdi32.dll!'+n].fn.call({g},...a);
    const dc=call('CreateCompatibleDC',0), bmi=g.alloc(44), out=g.alloc(4), tm=g.alloc(64);
    g.u32(bmi,40);g.u32(bmi+4,32);g.u32(bmi+8,-32);dv.setUint16(bmi+12,1,true);dv.setUint16(bmi+14,32,true);
    call('SelectObject',dc,call('CreateDIBSection',dc,bmi,0,out,0,0));
    call('SetTextColor',dc,0xffffff);call('SetBkColor',dc,0);
    const result=[]; const clipped=[];
    for(const family of ['Lucida Console','Tahoma'])for(const px of [-10,-13,9,10,11,12,13,14,16,20]){
      call('SelectObject',dc,call('CreateFontA',px,0,0,0,400,0,0,0,1,0,0,4,0,g.cstr(family)));
      call('GetTextMetricsA',dc,tm);const bits=g.u32(out);bytes.fill(0,bits,bits+4096);
      call('ExtTextOutA',dc,0,0,0,0,g.cstr('_'),1,0);
      const gm=g.alloc(20), need=call('GetGlyphOutlineA',dc,95,6,gm,0,0,0), buffer=g.alloc(Math.max(1,need));call('GetGlyphOutlineA',dc,95,6,gm,need,buffer,0);
      for(let ch=33;ch<127;ch++) {
        const metric=g.alloc(20); call('GetGlyphOutlineA',dc,ch,0,metric,0,0,0);
        const origin=g.i32(metric+12), height=g.u32(metric+4), descent=g.u32(tm+8);
        if(height-origin>descent || origin>g.u32(tm+4)) clipped.push({family,px,ch,origin,height,descent,ascent:g.u32(tm+4)});
      }
      result.push({outlineInk:bytes.subarray(buffer,buffer+need).some(v=>v),top:g.i32(gm+12),bottom:g.u32(gm+4),family,px,cell:g.u32(tm),ascent:g.u32(tm+4),descent:g.u32(tm+8),ink:bytes.subarray(bits,bits+g.u32(tm)*128).some(v=>v)});
    }
    return {result,clipped};
  })()`);
  console.log(JSON.stringify(result));
  assert.ok(result.result.every(r => r.ink && r.outlineInk), 'underscore must survive ExtTextOut and GetGlyphOutline');
  assert.deepEqual(result.clipped, [], 'GDI cell must contain every ASCII glyph');
} finally {if(browser)await browser.close();server.kill();}
