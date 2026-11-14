import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createRealm} from '../host.mjs';

export const install=process.env.GMOD_INSTALL || path.join(os.homedir(),'.local/share/Steam/steamapps/common/GarrysMod');
export function coreFiles() {
  const files=new Map();
  for(const name of ['hook','list']) {
    const rel=`lua/includes/modules/${name}.lua`;
    files.set(rel,fs.readFileSync(path.join(install,'garrysmod',rel)));
  }
  return files;
}
export const coreAssertions=`
  local hook=require("hook")
  local n=0
  hook.Add("Test","adder",function(v) n=n+v end)
  hook.Run("Test",3)
  assert(n==3)
  hook.Remove("Test","adder")
  hook.Run("Test",9)
  assert(n==3)
  hook.Add("Override","override",function() return false,42 end)
  local a,b=hook.Call("Override",{Override=function() error("fallback ran") end})
  assert(a==false && b==42)
  assert(hook.Call("Fallback",{Fallback=function(self,x) return x*2 end},5)==10)
  local owner={IsValid=function() return true end}
  hook.Add("Owner",owner,function(self) assert(self==owner); n=n+1 end)
  hook.Run("Owner"); assert(n==4)
  owner.IsValid=function() return false end
  hook.Run("Owner"); assert(n==4 && hook.GetTable().Owner[owner]==nil)
  local list=require("list")
  list.Set("Vehicles","test",{nested={value=5}})
  local copy=list.Get("Vehicles");copy.test.nested.value=9
  assert(list.GetEntry("Vehicles","test").nested.value==5)
  assert(list.HasEntry("Vehicles","test"))
  list.RemoveEntry("Vehicles","test");assert(!list.HasEntry("Vehicles","test"))
  assert(require("hook")==hook && require("list")==list)
  assert(bit.band(0xffffffff,15)==15 && bit.bnot(0)==-1)
  assert(bit.rshift(-1,1)==2147483647 && bit.arshift(-1,1)==-1)
`;
test('executes installed Facepunch hook/list modules with real GLua behavior', async () => {
  const r=await createRealm('server',{files:coreFiles()});
  try {r.run(coreAssertions);}
  finally {r.close();}
});
