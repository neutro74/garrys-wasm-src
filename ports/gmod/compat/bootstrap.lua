-- Compatibility primitives, not mock engine APIs. Unbound engine functions
-- intentionally stay absent and raise normal Lua errors when an addon uses them.
unpack = table.unpack
table.getn = function(t) return #t end
isstring = function(v) return type(v)=="string" end
isnumber = function(v) return type(v)=="number" end
isbool = function(v) return type(v)=="boolean" end
isfunction = function(v) return type(v)=="function" end
istable = function(v) return type(v)=="table" end
IsValid = function(v) return (type(v)=="table" or type(v)=="userdata") and isfunction(v.IsValid) and v:IsValid() or false end
ErrorNoHaltWithStack = function(...) print(...) end
ErrorNoHalt = ErrorNoHaltWithStack
Msg = function(...) print(...) end
MsgN = Msg

local loaded = {}
package={loaded=loaded,preload={}}
package.seeall=function(env)
  local mt=getmetatable(env) or {};mt.__index=_G;setmetatable(env,mt)
end

concommand={_callbacks={}}
function concommand.Add(name,fn) assert(isstring(name) and isfunction(fn));concommand._callbacks[name]=fn end
function concommand.Remove(name) concommand._callbacks[name]=nil end
function concommand.GetTable() return concommand._callbacks end
function concommand.Run(ply,name,args,text)
  local fn=concommand._callbacks[name];if !fn then return false end
  fn(ply,name,args or {},text or "");return true
end
function require(name)
  if loaded[name] != nil then return loaded[name] end
  if !isstring(name) or !name:match("^[%w_]+$") then error("Invalid Lua module name",2) end
  local result=include("includes/modules/" .. name .. ".lua")
  if result==nil then result=_G[name] or true end
  loaded[name]=result
  return result
end

function table.Copy(source, seen)
  if !istable(source) then return source end
  seen=seen or {}
  if seen[source] then return seen[source] end
  local out={}; seen[source]=out
  for k,v in pairs(source) do out[table.Copy(k,seen)]=table.Copy(v,seen) end
  return setmetatable(out,getmetatable(source))
end
function table.GetKeys(t) local out={}; for k in pairs(t) do out[#out+1]=k end; return out end
function table.Count(t) local n=0; for _ in pairs(t) do n=n+1 end; return n end
function table.IsEmpty(t) return next(t)==nil end
do
  local meta=getmetatable("")
  meta.__index=function(text,key)
    if isnumber(key) then return string.sub(text,key,key) end
    return string[key]
  end
end

-- Timers advance only with host game time, including pause behavior.
timer={}
do
  local timers,serial={},0
  function timer.Create(name,delay,repetitions,fn)
    assert(isstring(name) and isnumber(delay) and delay>=0 and isnumber(repetitions) and repetitions>=0 and isfunction(fn),"invalid timer")
    timers[name]={delay=delay,left=repetitions,infinite=repetitions==0,fn=fn,next=CurTime()+delay}
  end
  function timer.Simple(delay,fn) serial=serial+1;timer.Create("__simple_"..serial,delay,1,fn) end
  function timer.Remove(name) timers[name]=nil end
  function timer.Exists(name) return timers[name]!=nil end
  function timer.Pause(name)
    local t=timers[name];if !t or t.paused then return false end
    t.paused=t.next-CurTime();return true
  end
  function timer.UnPause(name)
    local t=timers[name];if !t or t.paused==nil then return false end
    t.next=CurTime()+t.paused;t.paused=nil;return true
  end
  function timer._tick()
    local due={}
    for name,t in pairs(timers) do if !t.paused and CurTime()>=t.next then due[#due+1]={name,t} end end
    for _,item in ipairs(due) do
      local name,t=item[1],item[2]
      if timers[name]!=t then continue end
      if !t.infinite then t.left=t.left-1 end
      if !t.infinite and t.left==0 then timers[name]=nil else t.next=CurTime()+t.delay end
      local ok,err=pcall(t.fn);if !ok then ErrorNoHaltWithStack(err) end
    end
  end
end

-- Local undo semantics. Client notifications/save integration remain separate.
undo={}
do
  local current,queues=nil,{}
  function undo.Create(name) current={Name=name,Entities={},Functions={}} end
  function undo.AddEntity(ent) if current and IsValid(ent) then table.insert(current.Entities,ent) end end
  function undo.AddFunction(fn,...) if current then table.insert(current.Functions,{fn,{...}}) end end
  function undo.SetPlayer(ply) if current then current.Owner=ply end end
  function undo.SetCustomUndoText(text) if current then current.CustomUndoText=text end end
  function undo.GetTable() return queues end
  function undo.Finish(niceText)
    if !current or (table.IsEmpty(current.Entities) and table.IsEmpty(current.Functions)) then current=nil;return false end
    local key=IsValid(current.Owner) and current.Owner:EntIndex() or 0
    queues[key]=queues[key] or {};current.NiceText=niceText or current.Name
    table.insert(queues[key],current);current=nil;return true
  end
  function undo.Do_Undo(entry)
    if !entry then return false end
    local count=0
    for _,call in ipairs(entry.Functions or {}) do if call[1](entry,unpack(call[2]))!=false then count=count+1 end end
    for _,ent in ipairs(entry.Entities or {}) do if IsValid(ent) then ent:Remove();count=count+1 end end
    return count
  end
  function undo.Undo(ply)
    local key=IsValid(ply) and ply:EntIndex() or 0
    local queue=queues[key];if !queue then return false end
    while #queue>0 do local count=undo.Do_Undo(table.remove(queue));if count and count>0 then return count end end
    return false
  end
end

local function signed(n) n=n & 0xffffffff; if n>=0x80000000 then return n-0x100000000 end; return n end
bit={}
bit.tobit=signed
bit.bnot=function(n) return signed(~n) end
bit.band=function(n,...) for _,v in ipairs({...}) do n=n & v end; return signed(n) end
bit.bor=function(n,...) for _,v in ipairs({...}) do n=n | v end; return signed(n) end
bit.bxor=function(n,...) for _,v in ipairs({...}) do n=n ~ v end; return signed(n) end
bit.lshift=function(n,s) return signed(n << (s & 31)) end
bit.rshift=function(n,s) return (n & 0xffffffff) >> (s & 31) end
bit.arshift=function(n,s) return signed(math.floor(signed(n)/2^(s & 31))) end

-- Restrict load to portable text; LuaJIT/retail bytecode is not compatible.
local originalLoad=load
load=function(chunk,name,mode,env) return originalLoad(chunk,name,"t",env or _G) end
CompileString=function(text,name,handleError)
  local fn,err=load(text,name)
  if !fn and handleError!=false then error(err,2) end
  return fn or err
end
