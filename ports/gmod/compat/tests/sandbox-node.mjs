// Node-side load test for sandbox.lua: mounts the real installed GMod Lua tree and runs
// the startup without the engine, with Lua stand-ins for the native Source bindings.
// This is a fast iteration harness; the authoritative test is tests/sandbox-browser.mjs.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRealm } from '../host.mjs';

const install = process.env.GMOD_INSTALL || path.join(os.homedir(), '.local/share/Steam/steamapps/common/GarrysMod');
const gm = path.join(install, 'garrysmod');
const here = path.resolve(import.meta.dirname, '..');

const files = new Map();
function walk(dir, rel) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const abs = path.join(dir, e.name);
    const r = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) walk(abs, r);
    else if (e.name.endsWith('.lua')) files.set(`lua/${r}`, fs.readFileSync(abs));
  }
}
walk(path.join(gm, 'lua'), '');
walk(path.join(gm, 'gamemodes'), 'gamemodes');
files.set('lua/startup.lua', fs.readFileSync(path.join(here, 'sandbox.lua')));

// Provided by the Source plugin in the real engine.
const prelude = `
function Vector(x,y,z)
  local v={x=x or 0,y=y or 0,z=z or 0}
  setmetatable(v,{__index={
    Length=function(s) return math.sqrt(s.x*s.x+s.y*s.y+s.z*s.z) end,
    GetNormalized=function(s) local l=s:Length();if l==0 then return Vector(0,0,0) end;return Vector(s.x/l,s.y/l,s.z/l) end,
    Dot=function(s,o) return s.x*o.x+s.y*o.y+s.z*o.z end,
    DistToSqr=function(s,o) local dx=s.x-o.x;local dy=s.y-o.y;local dz=s.z-o.z;return dx*dx+dy*dy+dz*dz end,
    Distance=function(s,o) return (s-o):Length() end,
  },__add=function(a,b) return Vector(a.x+b.x,a.y+b.y,a.z+b.z) end,
    __sub=function(a,b) return Vector(a.x-b.x,a.y-b.y,a.z-b.z) end,
    __mul=function(a,b) if isnumber(b) then return Vector(a.x*b,a.y*b,a.z*b) end;return Vector(a.x*b.x,a.y*b.y,a.z*b.z) end,
    __eq=function(a,b) return a.x==b.x and a.y==b.y and a.z==b.z end})
  return v
end
isvector=function(v) return istable(v) and v.x~=nil end
ents={Create=function() return nil end,GetAll=function() return {} end,FindByClass=function() return {} end,FindInSphere=function() return {} end}
player={GetAll=function() return {} end}
constraint={Weld=function() return nil end}
util={TraceLine=function() return {Hit=false} end,TraceHull=function() return {Hit=false} end}
game={GetWorld=function() return nil end}
print('[node-probe] prelude mocks installed')
`;
const realm = await createRealm('server', { files, print: (...a) => console.log(...a), printErr: (...a) => console.error(...a) });
try {
  realm.run(prelude, '@node-mocks');
  realm.include('startup.lua');
  console.log('[node-probe] startup.lua completed');
} catch (e) {
  console.error('[node-probe] startup failed:', e.message);
  process.exitCode = 1;
} finally {
  realm.close();
}
