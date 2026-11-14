import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRealm } from '../host.mjs';

test('executes GLua syntax without rewriting strings or comments', async () => {
  const r = await createRealm('server');
  try {
    r.run(`// GLua line comment
      /* block comment */
      assert(!false && true || false)
      assert(1 != 2 and 1 ~= 2)
      assert((6 & 3) == 2)
      assert("! != && || // /* continue" == [=[! != && || // /* continue]=])
      local n = 0
      for i=1,6 do if i % 2 == 0 then continue end; n=n+i end
      assert(n == 9)
      local j=0
      while j < 5 do j=j+1; if j<5 then continue end; n=n+1 end
      assert(n == 10)
      n=0
      for _, v in ipairs({1,2,3}) do
        for k=1,3 do if k==2 then continue end; n=n+v end
      end
      assert(n == 12)
      local k=0
      repeat k=k+1; if k<3 then continue end until k==3
      assert(k==3)
      local funcs={}
      for i=1,3 do local v=i; funcs[i]=function() return v end; continue end
      assert(funcs[1]()==1 && funcs[3]()==3)
    `);
    assert.throws(() => r.run('continue'), /continue|label/);
    assert.throws(() => r.run('/* unterminated'), /comment/);
    assert.throws(() => r.compile('return function() continue end', 'invalid-loop'), /continue|label/);
  } finally { r.close(); }
});

test('mounts includes, isolates realms, runs hooks and recovers after errors', async () => {
  const files = new Map([
    ['lua/autorun/common.lua', 'include("parts/value.lua"); sharedCount=(sharedCount or 0)+1'],
    ['lua/autorun/parts/value.lua', 'value=include("nested.lua")'],
    ['lua/autorun/parts/nested.lua', 'return 42'],
    ['lua/autorun/server/s.lua', 'serverOnly=true'],
    ['lua/autorun/client/c.lua', 'clientOnly=true'],
  ]);
  const s = await createRealm('server', { files });
  const c = await createRealm('client', { files });
  try {
    assert.deepEqual(s.autorun().failed, []);
    assert.deepEqual(c.autorun().failed, []);
    s.run('assert(SERVER && !CLIENT && serverOnly && !clientOnly && value==42 && sharedCount==1)');
    c.run('assert(CLIENT && !SERVER && clientOnly && !serverOnly && value==42 && sharedCount==1)');
    assert.throws(() => s.run('include("../../escape.lua")'), /path/);
    assert.throws(() => s.run('while true do end'), /budget/);
    assert.throws(() => s.run('error("expected failure")'), /expected failure/);
    s.run('assert(2+2==4)');
    s.run('assert(io==nil && os==nil && package.loadlib==nil)');
  } finally { s.close(); c.close(); }
});

test('supports Lua 5.1 module environments and game-time timers', async () => {
  const r=await createRealm('server',{files:new Map([
    ['lua/includes/modules/legacy.lua','module("legacy",package.seeall); function Answer() return math.floor(42.1) end'],
  ])});
  try {
    r.run('local m=require("legacy");assert(m.Answer()==42 && Answer==nil); assert(("abc")[2]=="b")');
    r.run('ticks=0;timer.Create("test",0.2,2,function()ticks=ticks+1 end)');
    r.setTime(0.1);r.run('timer._tick();assert(ticks==0)');
    r.setTime(0.21);r.run('timer._tick();assert(ticks==1)');
    r.setTime(0.42);r.run('timer._tick();assert(ticks==2 && !timer.Exists("test"))');
    r.setTime(1);r.run('timer._tick();assert(ticks==2)');
  }finally{r.close();}
});

test('reports addon errors while continuing other autorun files', async () => {
  const r = await createRealm('server', { files: new Map([
    ['lua/autorun/a.lua', 'error("broken addon")'],
    ['lua/autorun/b.lua', 'loadedSecond=true'],
  ]) });
  try {
    const result = r.autorun();
    assert.equal(result.failed.length, 1);
    assert.match(result.failed[0].error, /broken addon/);
    r.run('assert(loadedSecond)');
  } finally { r.close(); }
});

test('compiles large addon sources without overflowing the C argument stack', async () => {
  const r=await createRealm('server');
  try {
    r.compile('--'+ 'x'.repeat(300000)+'\nlocal x=42;return x');
    r.run('large="'+ 'y'.repeat(200000)+'"; assert(#large==200000)');
    r.run('assert(1+1==2)');
  } finally {r.close();}
});
