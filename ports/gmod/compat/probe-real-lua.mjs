// Probe: which real installed GMod Lua files execute under the current GLua runtime?
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createRealm} from './host.mjs';
const install=process.env.GMOD_INSTALL || path.join(os.homedir(),'.local/share/Steam/steamapps/common/GarrysMod');
const gm=path.join(install,'garrysmod');
const files=new Map();
function walk(dir,rel){for(const e of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,e.name);const r=rel?rel+'/'+e.name:e.name;if(e.isDirectory())walk(p,r);else if(e.name.endsWith('.lua'))files.set('lua/'+r,fs.readFileSync(p));}}
walk(path.join(gm,'lua'),'');
walk(path.join(gm,'gamemodes'),'gamemodes');
// Only mount files whose paths are lua-safe.
const realm=await createRealm('server',{files});
const candidates=[
 'autorun/base_npcs.lua','autorun/base_vehicles.lua','autorun/game_hl2.lua','autorun/menubar.lua',
 'autorun/properties.lua','autorun/server/admin_functions.lua',
 'gamemodes/base/gamemode/init.lua','gamemodes/sandbox/gamemode/shared.lua',
 'gamemodes/sandbox/gamemode/commands.lua','gamemodes/sandbox/gamemode/player.lua',
 'gamemodes/sandbox/gamemode/init.lua',
 'includes/modules/hook.lua','includes/modules/list.lua','includes/modules/gamemode.lua',
];
const out={mounted:files.size,results:[]};
for(const c of candidates){
  try{realm.run(`include(${JSON.stringify(c)})`);out.results.push({file:c,ok:true});}
  catch(e){out.results.push({file:c,ok:false,error:e.message.split('\n')[0]});}
}
// report hook table + global functions after all attempts
try{realm.run(`_G.__probe_hook_count=0; local t=hook and hook.GetTable() or {}; for k,v in pairs(t) do _G.__probe_hook_count=_G.__probe_hook_count+1 end`);}catch{}
console.log(JSON.stringify(out,null,2));
for(const r of out.results)console.log((r.ok?'OK  ':'FAIL')+' '+r.file+(r.error?'  <- '+r.error:''));
realm.close();
