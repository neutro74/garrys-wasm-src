import {Guest,SHIMS} from './lift/host/guest.mjs';import {OpBackend} from './lift/host/d3d9-backend.mjs';
const g=new Guest({pages:512}),dll={meta:{spare_thunks:1024,thunk_base:0x13000000,imports:[]},extras:[]};class Backend extends OpBackend{emit(){}}const be=g.d3d9Backend=new Backend();
const d3d=SHIMS['d3d9.dll!Direct3DCreate9'].fn.call({g,dll},32),rt=g.d3dRuntime;
const call=(obj,slot,...args)=>{const st=rt.objects.get(obj);return dll.extras.find(x=>x?.name===`d3d9#${st.kind}:${slot}`).fn.call({g,dll},obj,...args);};
const pp=g.alloc(56),out=g.alloc(4);g.u32(pp,64);g.u32(pp+4,64);g.u32(pp+8,21);g.u32(pp+32,1);call(d3d,16,0,1,0,0,pp,out);const dev=rt.objects.get(g.u32(out));
be.stateDelta(dev);for(let i=0;i<1000;i++)be.stateDelta(dev);const start=performance.now();for(let i=0;i<20000;i++)be.stateDelta(dev);console.log(JSON.stringify({calls:20000,ms:performance.now()-start}));
