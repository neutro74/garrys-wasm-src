// Probe: which real installed addon server/authorun chunks load under the current runtime?
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {readGma} from './addons.mjs';
import {createRealm} from './host.mjs';
const install=process.env.GMOD_INSTALL || path.join(os.homedir(),'.local/share/Steam/steamapps/common/GarrysMod');
const root=path.join(process.env.HOME,'.local/share/Steam/steamapps/workshop/content/4000');
const core=new Map();
for(const n of ['hook','list'])core.set('lua/includes/modules/'+n+'.lua',fs.readFileSync(path.join(install,'garrysmod/lua/includes/modules/'+n+'.lua')));
const cases=[];
for(const id of fs.readdirSync(root).sort()){
  const dir=path.join(root,id);if(!fs.statSync(dir).isDirectory())continue;
  const gma=fs.readdirSync(dir).find(f=>f.endsWith('.gma'));if(!gma)continue;
  const file=path.join(dir,gma);const size=fs.statSync(file).size;
  if(size>2*1024*1024)continue;
  let a;try{a=readGma(fs.readFileSync(file));}catch(e){continue;}
  const files=new Map();
  for(const [n,data] of a.files)files.set(n,data);
  cases.push({id,name:a.name,files});
}
const out=[];
for(const c of cases){
  const realm=await createRealm('server',{files:new Map([...core,...c.files]),print:()=>{},printErr:()=>{}});
  const autoruns=[...c.files.keys()].filter(p=>/^lua\/autorun\/[^/]+\.lua$|^lua\/autorun\/server\/[^/]+\.lua$/.test(p)).sort();
  const loaded=[],failed=[];
  for(const p of autoruns){try{realm.run(`include(${JSON.stringify(p.slice(4))})`);loaded.push(p);}catch(e){failed.push({path:p,error:e.message.split('\n')[0]});}}
  let hooks=0;try{realm.run('hookCount=0;for k in pairs(hook.GetTable())do hookCount=hookCount+1 end');hooks=realm.run('return hookCount');}catch{}
  out.push({id:c.id,name:c.name,autoruns:autoruns.length,loaded,failed,hookCount:hooks&&hooks.value});
  realm.close();
}
for(const r of out.filter(r=>r.loaded.length||r.failed.length))console.log(r.id,(r.name||'').slice(0,30),'loaded',r.loaded.length,'failed',r.failed.length,r.failed.slice(0,1).map(f=>f.error));
fs.writeFileSync('ports/gmod/compat/evidence/addon-autorun-probe.json',JSON.stringify(out,null,2));
