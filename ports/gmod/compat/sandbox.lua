-- GameBox sandbox startup. Loaded by the Source compatibility plugin as
-- /gamebox-gmod/lua/startup.lua after bootstrap.lua and after the native Source
-- bindings are installed.
--
-- Layers, in order:
--   1. compatibility shims for GMod Lua APIs that the Source bridge does not (yet)
--      back natively (convars, game/util tables, module namespace helpers),
--   2. the real installed GMod Lua modules (hook, list, baseclass, gamemode, ...),
--   3. the real installed sandbox gamemode server Lua (gamemodes/base + sandbox),
--   4. real mounted addon autorun files (server realm order: autorun/*.lua then
--      autorun/server/*.lua), listed by the host in gamebox-addons.lua,
--   5. the sandbox gameplay commands (Lua-driven spawn/grab/freeze/weld/undo).
--
-- Everything below runs in the server realm (SERVER == true, CLIENT == false).

local function join(...)
  local parts = {}
  for i = 1, select('#', ...) do parts[i] = tostring((select(i, ...))) end
  return table.concat(parts, ' ')
end
local function msg(...) print('[gmod-sandbox] ' .. join(...)) end
local loaded, failed = {}, {}
local function tryInclude(path, tag)
  local ok, err = pcall(include, path)
  if ok then
    loaded[#loaded + 1] = path
    msg(tag or 'loaded', path)
  else
    failed[#failed + 1] = { path = path, error = tostring(err) }
    msg('FAILED', path, ':', tostring(err))
  end
  return ok, err
end
local function tryRequire(name)
  local ok, err = pcall(require, name)
  if ok then
    loaded[#loaded + 1] = 'module:' .. name
    msg('module', name)
  else
    failed[#failed + 1] = { path = 'module:' .. name, error = tostring(err) }
    msg('MODULE FAILED', name, ':', tostring(err))
  end
  return ok
end

--------------------------------------------------------------------------
-- 1. Compatibility shims.
--------------------------------------------------------------------------

-- GMod's table helpers (baseclass/gamemode modules use them at load time).
if not table.Merge then
  function table.Merge(dst, src)
    if dst == src then return dst end
    for k, v in pairs(src) do
      if istable(v) and istable(dst[k]) then table.Merge(dst[k], v) else dst[k] = v end
    end
    return dst
  end
end
if not table.Inherit then
  function table.Inherit(t, base)
    local out = setmetatable({}, { __index = base })
    for k, v in pairs(t) do out[k] = v end
    return out
  end
end
if not table.Empty then
  function table.Empty(t)
    for k in pairs(t) do t[k] = nil end
    return t
  end
end

-- Console variable shim backed by a Lua cvar registry. The native Source side is
-- not bound for these yet; the shim keeps values and honours the GMod API shape.
FCVAR_NONE = 0
FCVAR_UNREGISTERED = 1
FCVAR_DEVELOPMENTONLY = 2
FCVAR_GAMEDLL = 4
FCVAR_CLIENTDLL = 8
FCVAR_HIDDEN = 16
FCVAR_PROTECTED = 32
FCVAR_SPONLY = 64
FCVAR_ARCHIVE = 128
FCVAR_NOTIFY = 256
FCVAR_USERINFO = 512
FCVAR_NEVER_AS_STRING = 1024
FCVAR_REPLICATED = 8192
FCVAR_CHEAT = 16384
FCVAR_DEMO = 65536
FCVAR_DONTRECORD = 131072
FCVAR_NOT_CONNECTED = 262144
FCVAR_ARCHIVE_XBOX = 16777216
FCVAR_SERVER_CAN_EXECUTE = 268435456
FCVAR_CLIENTCMD_CAN_EXECUTE = 536870912
FCVAR_CHEATDLL = FCVAR_CHEAT
FCVAR_RELOAD_MATERIALS = 0
FCVAR_RELOAD_TEXTURES = 0
FCVAR_MATERIAL_SYSTEM_THREAD = 0
FCVAR_ACCESSIBLE_FROM_THREADS = 0

local cvarList = {}
local function cvarObject(name, value)
  local c = { Name = name, Value = value }
  function c:GetName() return self.Name end
  function c:GetString() return tostring(self.Value == nil and '' or self.Value) end
  function c:GetFloat() return tonumber(self.Value) or 0 end
  function c:GetInt() return math.floor(tonumber(self.Value) or 0) end
  function c:GetBool() return (tonumber(self.Value) or 0) ~= 0 end
  function c:SetString(v) self.Value = v end
  function c:SetFloat(v) self.Value = v end
  function c:SetInt(v) self.Value = v end
  function c:SetBool(v) self.Value = v and 1 or 0 end
  function c:GetDefault() return self.Default end
  function c:GetMin() return 0 end
  function c:GetMax() return 0 end
  function c:IsCommand() return false end
  function c:GetFlags() return self.Flags or 0 end
  c.Default = value
  return c
end
CreateConVar = CreateConVar or function(name, default, flags, help)
  local c = cvarList[name] or cvarObject(name, default)
  if cvarList[name] then c.Value = default end
  c.Flags = flags
  cvarList[name] = c
  return c
end
GetConVar = GetConVar or function(name)
  return cvarList[name] or cvarObject(name, 0)
end
ConVarExists = ConVarExists or function(name) return cvarList[name] ~= nil end
GetConVarString = GetConVarString or function(name) return GetConVar(name):GetString() end
GetConVarNumber = GetConVarNumber or function(name) return GetConVar(name):GetFloat() end
cvars = cvars or {
  Bool = function(name, default)
    local c = cvarList[name]
    if c == nil then return default end
    return c:GetBool()
  end,
  Number = function(name, default)
    local c = cvarList[name]
    if c == nil then return default end
    return c:GetFloat()
  end,
  String = function(name, default)
    local c = cvarList[name]
    if c == nil then return default end
    return c:GetString()
  end,
  AddChangeCallback = function() end,
}

-- gmod namespace shim (the gamemode module captures it at load time).
GM = GM or {}
GAMEMODE = GM
gmod = gmod or {}
function gmod.GetGamemode() return GAMEMODE end
function gmod.GetGamemodeName() return 'sandbox' end
function gmod.GetGamemodeFolderName() return 'sandbox' end
function gmod.GetGamemodes() return { base = true, sandbox = true } end
function gmod.GetGamemodesList() return { 'base', 'sandbox' } end
function gmod.AddNotify() end
gmod.Map = gmod.Map or 'gm_construct'

-- game table: the plugin already bound game.GetWorld; extend it.
if not game then game = {} end
game.SinglePlayer = game.SinglePlayer or function() return true end
game.IsSingleplayer = game.SinglePlayer
game.GetMap = game.GetMap or function() return GetGlobalString and GetGlobalString('gamebox_map') or 'gm_construct' end
game.AddParticles = game.AddParticles or function() end
game.ConsoleCommand = game.ConsoleCommand or function() end
game.GetTimeScale = game.GetTimeScale or function() return 1 end
game.SetTimeScale = game.SetTimeScale or function() end
game.IPAddress = game.IPAddress or function() return '127.0.0.1' end
game.GetIPAddress = game.IPAddress
game.IsDedicated = game.IsDedicated or function() return false end
game.MaxPlayers = game.MaxPlayers or function() return 1 end

-- util: the plugin binds util.TraceLine; fill in the other GMod helpers addons use.
if not util then util = {} end
util.AddNetworkString = util.AddNetworkString or function() end
util.Effect = util.Effect or function() end
util.Decal = util.Decal or function() end
util.DecalEx = util.DecalEx or function() end
util.BlastDamage = util.BlastDamage or function() end
util.BlastDamageInfo = util.BlastDamageInfo or function() end
util.GetPlayerTrace = util.GetPlayerTrace or function() return { start = Vector(0, 0, 0), endpos = Vector(0, 0, 0) } end
util.TraceLine = util.TraceLine or function() return { Hit = false } end
util.TraceHull = util.TraceHull or function() return { Hit = false } end
util.TraceEntity = util.TraceEntity or util.TraceLine
util.IsInWorld = util.IsInWorld or function() return true end
util.TableToKeyValues = util.TableToKeyValues or function() return '' end
util.KeyValuesToTable = util.KeyValuesToTable or function() return {} end
util.PrecacheModel = util.PrecacheModel or function() end
util.PrecacheSound = util.PrecacheSound or function() end
util.SpriteTrail = util.SpriteTrail or function() return 0 end
util.PointContents = util.PointContents or function() return 0 end

-- sound: the native GMod sound library is not bound; log the hints addons emit.
if not sound then sound = {} end
local hints = 0
sound.EmitHint = sound.EmitHint or function(kind, pos, radius)
  hints = hints + 1
  if hints <= 5 then msg('sound.EmitHint', kind, 'at', tostring(pos)) end
end
sound.Play = sound.Play or function() end
sound.PlayFile = sound.PlayFile or function() end
sound.Add = sound.Add or function() end
sound.Precache = sound.Precache or function() end

-- net / resource / file-ish namespaces used by addons at load time.
if not net then net = {} end
net.Start = net.Start or function() end
net.Send = net.Send or function() end
net.Broadcast = net.Broadcast or function() end
net.Receive = net.Receive or function() end
net.WriteString = net.WriteString or function() end
net.WriteUInt = net.WriteUInt or function() end
net.WriteInt = net.WriteInt or function() end
net.WriteFloat = net.WriteFloat or function() end
net.WriteBool = net.WriteBool or function() end
net.WriteTable = net.WriteTable or function() end
net.WriteVector = net.WriteVector or function() end
net.ReadString = net.ReadString or function() return '' end
net.ReadUInt = net.ReadUInt or function() return 0 end
net.ReadInt = net.ReadInt or function() return 0 end
net.ReadFloat = net.ReadFloat or function() return 0 end
net.ReadBool = net.ReadBool or function() return false end
net.ReadTable = net.ReadTable or function() return {} end
net.SendToServer = net.SendToServer or function() end
net.Incoming = net.Incoming or function() end
net.WriteDouble = net.WriteDouble or function() end
net.WriteEntity = net.WriteEntity or function() end
net.WriteAngle = net.WriteAngle or function() end
net.WriteVar = net.WriteVar or function() end
net.WriteType = net.WriteType or function() end
net.WriteMatrix = net.WriteMatrix or function() end
net.ReadDouble = net.ReadDouble or function() return 0 end
net.ReadEntity = net.ReadEntity or function() return nil end
net.ReadAngle = net.ReadAngle or function() return Angle(0, 0, 0) end
net.ReadVar = net.ReadVar or function() return nil end
net.ReadType = net.ReadType or function() return 0 end
net.ReadData = net.ReadData or function() return '' end

if not resource then resource = {} end
resource.AddSingleFile = resource.AddSingleFile or function() end
resource.AddFile = resource.AddFile or function() end

if not file then file = {} end
file.Exists = file.Exists or function() return false end
file.Read = file.Read or function() return nil end
file.Find = file.Find or function() return {}, {} end
file.CreateDir = file.CreateDir or function() end
file.Open = file.Open or function() return nil end
file.Write = file.Write or function() return false end
file.Add = file.Add or function() end
file.AddWorkshop = file.AddWorkshop or function() end
file.Delete = file.Delete or function() return false end
file.Time = file.Time or function() return 0 end
file.Size = file.Size or function() return 0 end
file.IsDir = file.IsDir or function() return false end

if not debugoverlay then debugoverlay = {} end
debugoverlay.Line = debugoverlay.Line or function() end
debugoverlay.Box = debugoverlay.Box or function() end
debugoverlay.Text = debugoverlay.Text or function() end
debugoverlay.Sphere = debugoverlay.Sphere or function() end

if not effects then effects = {} end
effects.Create = effects.Create or function() return { entity = nil, data = {} } end
EffectData = EffectData or function() return setmetatable({}, { __index = function() return function() end end }) end

-- Color / Angle / misc value types (Vector and entity userdata are native).
if not Color then
  Color = function(r, g, b, a) return { r = r or 255, g = g or 255, b = b or 255, a = a or 255 } end
end
if not Angle then
  Angle = function(p, y, r) return { p = p or 0, y = y or 0, r = r or 0 } end
end
if not LerpVector then
  LerpVector = function(t, a, b) return a + (b - a) * t end
end
if not math.Clamp then
  function math.Clamp(v, lo, hi) return math.min(math.max(v, lo), hi) end
end
if not math.Rand then math.Rand = math.random end
if not string.Trim then
  function string.Trim(s) return (tostring(s):gsub('^%s+', ''):gsub('%s+$', '')) end
end
if not GetGlobalString then GetGlobalString = function() return nil end end
if not GetGlobalInt then GetGlobalInt = function() return 0 end end
if not IsFirstTimePredicted then IsFirstTimePredicted = function() return true end end
if not CLIENT and SERVER == nil then SERVER = true; CLIENT = false end

-- GMod library helpers the engine normally binds natively.
MsgC = MsgC or function(...)
  local parts = {}
  for i = 1, select('#', ...) do
    local v = select(i, ...)
    if type(v) ~= 'table' or v.r == nil then parts[#parts + 1] = tostring(v) end
  end
  print('[MsgC] ' .. table.concat(parts, ''))
end
MsgN = MsgN or function(...) print(...) end
PrecacheParticleSystem = PrecacheParticleSystem or function() end
FrameTime = FrameTime or function() return 1 / 66 end
RealFrameTime = RealFrameTime or FrameTime
FrameNumber = FrameNumber or function() return 0 end
SysTime = SysTime or function() return CurTime() end
IsFirstTimePredicted = IsFirstTimePredicted or function() return true end
if not system then
  system = {
    GetTime = function() return CurTime() end,
    AppTime = function() return CurTime() end,
    IsWindows = function() return false end,
    IsLinux = function() return true end,
    IsOSX = function() return false end,
    HasFocus = function() return true end,
    GetCPUCount = function() return 1 end,
    UpTime = function() return CurTime() end,
  }
end
-- LuaJIT shape: installed GMod Lua checks jit.arch; this runtime is plain Lua 5.1.
jit = jit or { version = 'Lua 5.1', version_num = 501, arch = 'x64', os = 'Linux',
  off = function() end, on = function() end, status = function() return false, '' end }
-- Sensor bone names used by the motionsensor extension's debug table.
if not SENSORBONE then
  SENSORBONE = setmetatable({}, { __index = function(t, k) rawset(t, k, k); return k end })
end
-- Restricted os shim: GMod exposes os to Lua; the portable runtime has no OS layer.
if not os then
  os = {
    time = function() return math.floor(CurTime()) end,
    clock = function() return CurTime() end,
    difftime = function(a, b) return a - b end,
    date = function(fmt) return '01/01/70 00:00:00' end,
    getenv = function() return nil end,
  }
end
-- GMod net type ids (used as table keys by duplicator/addons).
for name, id in pairs({
  TYPE_NONE = 0, TYPE_NIL = 0, TYPE_BOOL = 1, TYPE_NUMBER = 2, TYPE_STRING = 3, TYPE_TABLE = 4,
  TYPE_FUNCTION = 5, TYPE_USERDATA = 6, TYPE_THREAD = 7, TYPE_ENTITY = 8,
  TYPE_VECTOR = 9, TYPE_ANGLE = 10, TYPE_MATRIX = 11, TYPE_PHYSOBJ = 12,
  TYPE_SAVE = 13, TYPE_RESTORE = 14, TYPE_DAMAGEINFO = 15, TYPE_EFFECTDATA = 16,
  TYPE_SCRIPTEDVEHICLE = 17, TYPE_MATERIAL = 18, TYPE_CONVAR = 19, TYPE_IMATRIX = 20,
  TYPE_MESH = 21, TYPE_MOVEDATA = 22, TYPE_USERCMD = 23,
}) do _G[name] = _G[name] or id end
-- table helpers used by addons before the installed extensions are loaded.
if not table.Add then function table.Add(dst, src) for _, v in ipairs(src) do dst[#dst + 1] = v end return dst end end
if not table.HasValue then function table.HasValue(t, v) for _, x in ipairs(t) do if x == v then return true end end return false end end
if not table.RemoveByValue then
  function table.RemoveByValue(t, v)
    for i, x in ipairs(t) do if x == v then return table.remove(t, i) end end
  end
end
if not table.Random then
  function table.Random(t)
    if #t > 0 then return t[math.random(#t)] end
    local keys = {}; for k in pairs(t) do keys[#keys + 1] = k end
    if #keys == 0 then return nil end
    local k = keys[math.random(#keys)]; return t[k], k
  end
end
if not string.Explode then
  function string.Explode(sep, text, usePatterns) local out = {}; for part in string.gmatch(text, '([^' .. sep .. ']+)') do out[#out + 1] = part end; return out end
end
if not string.Left then string.Left = function(s, n) return string.sub(s, 1, n) end end
if not string.Right then string.Right = function(s, n) return string.sub(s, -n) end end
if not string.Replace then string.Replace = function(s, a, b) return (string.gsub(s, a:gsub('(%W)', '%%%1'), b)) end end
if not string.Trim then string.Trim = function(s) return (tostring(s):gsub('^%s+', ''):gsub('%s+$', '')) end end

-- Engine numeric constants the installed gamemode Lua reads at load time. GMod binds
-- these natively; the compatibility layer assigns stable values (only used as table keys
-- and for animation/activity selection in code paths the Source bridge does not play).
do
  local n = 100
  local function constants(prefix, names)
    for _, name in ipairs(names) do n = n + 1; _G[name] = _G[name] or n end
  end
  constants('ACT', {
    'ACT_MP_STAND_IDLE', 'ACT_MP_WALK', 'ACT_MP_RUN', 'ACT_MP_CROUCH_IDLE',
    'ACT_MP_CROUCHWALK', 'ACT_MP_ATTACK_STAND_PRIMARYFIRE',
    'ACT_MP_ATTACK_CROUCH_PRIMARYFIRE', 'ACT_MP_RELOAD_STAND',
    'ACT_MP_RELOAD_CROUCH', 'ACT_MP_JUMP', 'ACT_MP_SWIM', 'ACT_LAND',
    'ACT_HL2MP_IDLE', 'ACT_HL2MP_JUMP_SLAM', 'ACT_MP_JUMP_FLOAT',
    'ACT_MP_JUMP_LAND', 'ACT_MP_AIRWALK', 'ACT_MP_CROUCHWALK',
  })
  constants('TEAM', { 'TEAM_CONNECTING', 'TEAM_UNASSIGNED', 'TEAM_SPECTATOR' })
  constants('MOVETYPE', { 'MOVETYPE_NONE', 'MOVETYPE_NOCLIP', 'MOVETYPE_WALK',
    'MOVETYPE_STEP', 'MOVETYPE_VPHYSICS' })
  _G.TEAM_CONNECTING = 0
  _G.TEAM_UNASSIGNED = 1
  _G.TEAM_SPECTATOR = 2
  _G.MOVETYPE_NONE = 0
  _G.MOVETYPE_NOCLIP = 8
  _G.MOVETYPE_WALK = 2
  _G.MOVETYPE_STEP = 4
  _G.MOVETYPE_VPHYSICS = 6
end

-- Modules the runtime does not load from the install (they need native bindings or are
-- replaced by the compatibility layer): concommand (bootstrap version), sql/cookie,
-- duplicator/constraint (native bridge). Add shape-only fallbacks.
if not duplicator then
  duplicator = {
    RegisterBoneModifier = function() end, StoreBoneModifier = function() end,
    ApplyBoneModifier = function() end, RegisterEntityClass = function() end,
    RegisterConstraintClass = function() end, StoreEntityModifier = function() end,
    StoreConstraintModifier = function() end, RegisterGeneric = function() end,
  }
end
if not sql then
  sql = {
    SQLStr = function(s) return s end, SQLEscape = function(s) return s end,
    TableExists = function() return true end,
    Query = function() return {} end,
    QueryRow = function() return nil end, QueryValue = function() return nil end,
    Begin = function() end, Commit = function() end,
  }
end

-- Module namespace fallbacks. The real modules are preferred (loaded below); these
-- only exist so a missing module leaves the API shape a gamemode expects.
if not FindMetaTable then
  local fallback = {}
  FindMetaTable = function(name)
    fallback[name] = fallback[name] or {}
    return fallback[name]
  end
end
if not DEFINE_BASECLASS then
  function DEFINE_BASECLASS(name)
    BaseClass = (baseclass and baseclass.Get(name)) or BaseClass or {}
  end
end

--------------------------------------------------------------------------
-- 2. Real GMod Lua modules from the install.
--------------------------------------------------------------------------
-- Loading order follows garrysmod/lua/includes/init.lua, minus the modules whose
-- native side is the plugin's (undo, constraint) and the pure client ones.
local modules = {
  'baseclass', 'saverestore', 'hook', 'gamemode', 'weapons',
  'scripted_ents', 'player_manager', 'numpad', 'team', 'cleanup',
  'construct', 'usermessage', 'list', 'cvars', 'http', 'properties', 'widget',
  'utf8', 'ai_task', 'ai_schedule', 'drive',
}
for _, name in ipairs(modules) do
  if name == 'gamemode' and not gmod then break end
  tryRequire(name)
end

-- Drive modes: the real drive module plus the base/noclip drive classes it needs.
tryInclude('drive/drive_base.lua', 'drive:base')
tryInclude('drive/drive_noclip.lua', 'drive:noclip')

-- Real installed GMod Lua extensions (the same files lua/includes/init.lua loads).
for _, path in ipairs({
  'includes/extensions/table.lua', 'includes/extensions/string.lua',
  'includes/extensions/math.lua', 'includes/extensions/angle.lua',
  'includes/extensions/vector.lua', 'includes/extensions/util.lua',
  'includes/extensions/game.lua', 'includes/extensions/entity_iter.lua',
  'includes/extensions/entity.lua', 'includes/extensions/ents.lua',
  'includes/extensions/player.lua', 'includes/extensions/player_auth.lua',
  'includes/extensions/weapon.lua', 'includes/extensions/net.lua',
  'includes/extensions/file.lua', 'includes/extensions/debug.lua',
  'includes/extensions/motionsensor.lua', 'includes/extensions/coroutine.lua',
}) do
  tryInclude(path, 'extension')
end

-- Entity creation dispatches GMod's OnEntityCreated hook, like the engine does.
if ents and ents.Create and not ents._gamebox_wrapped then
  local nativeCreate = ents.Create
  ents._gamebox_wrapped = true
  function ents.Create(class)
    local ent = nativeCreate(class)
    if IsValid(ent) and hook then pcall(hook.Run, 'OnEntityCreated', ent) end
    return ent
  end
end

-- Hook dispatch instrumentation: log the first dispatches (name, callback owners) and any
-- error raised inside a callback. This is host instrumentation; the callbacks are the
-- unmodified installed addon/gamemode Lua.
if hook and hook.Call and not hook._gamebox_instrumented then
  hook._gamebox_instrumented = true
  local realCall = hook.Call
  local dispatches, errors = {}, 0
  function hook.Call(name, gm, ...)
    local count = (dispatches[name] or 0) + 1
    dispatches[name] = count
    if count <= 2 then
      local owners = {}
      local table_ = hook.GetTable()[name]
      for owner in pairs(table_ or {}) do owners[#owners + 1] = tostring(owner) end
      table.sort(owners)
      msg('hook dispatch', name, '#' .. count, 'callbacks=' .. #owners, '[' .. table.concat(owners, ',') .. ']')
    end
    local result = { pcall(realCall, name, gm, ...) }
    if not result[1] then
      errors = errors + 1
      msg('HOOK ERROR', name, tostring(result[2]))
      if errors == 20 then msg('further hook errors suppressed') end
      return nil
    end
    return unpack(result, 2)
  end
  function GameBoxHookStats()
    local names = {}
    for name, count in pairs(dispatches) do names[#names + 1] = name .. ':' .. count end
    table.sort(names)
    return table.concat(names, ',')
  end
end

-- Keep the plugin's native binding for constraint/weld and the bootstrap undo queue.
if not constraint then constraint = {} end
constraint.Weld = constraint.Weld or function() return nil end
if not undo then undo = {} end

--------------------------------------------------------------------------
-- 3. Real sandbox gamemode server Lua.
--------------------------------------------------------------------------
GM = {}
GAMEMODE = GM
local gamemodeLoaded = false
if not baseclass then baseclass = { Get = function() return {} end } end
if not DEFINE_BASECLASS then DEFINE_BASECLASS = function(name) BaseClass = baseclass.Get(name) end end

local baseGM = {}
GM = baseGM
GAMEMODE = GM
tryInclude('gamemodes/base/gamemode/init.lua', 'gamemode:base')
if gamemode and gamemode.Register then pcall(gamemode.Register, baseGM, 'base') end

GM = {}
GAMEMODE = GM
tryInclude('gamemodes/sandbox/gamemode/init.lua', 'gamemode:sandbox')
if gamemode and gamemode.Register then
  local ok = pcall(gamemode.Register, GM, 'sandbox', 'base')
  if ok then gamemodeLoaded = true; msg('gamemode registered: sandbox (base inherited)') end
end

--------------------------------------------------------------------------
-- 4. Real addon autorun files, listed by the host (realm-correct order).
--------------------------------------------------------------------------
local addonsLoaded, addonsFailed = 0, 0
do
  local ok, list = pcall(include, 'gamebox-addons.lua')
  if ok and type(list) == 'table' then
    msg('addon autorun manifest: ' .. tostring(#list) .. ' server files')
    for _, entry in ipairs(list) do
      local path = type(entry) == 'table' and entry.path or entry
      local ok2, err = pcall(include, path)
      if ok2 then
        addonsLoaded = addonsLoaded + 1
        msg('addon', path)
      else
        addonsFailed = addonsFailed + 1
        failed[#failed + 1] = { path = path, error = tostring(err) }
        msg('ADDON FAILED', path, ':', tostring(err))
      end
    end
  elseif not ok then
    msg('no addon autorun manifest (' .. tostring(list) .. ')')
  end
end

--------------------------------------------------------------------------
-- 5. Sandbox gameplay: native Source entities driven by Lua.
--------------------------------------------------------------------------
local held, selected = {}, {}

local function trace(ply)
  local start = ply:EyePos()
  return util.TraceLine({ start = start, endpos = start + ply:GetAimVector() * 1500, filter = ply })
end
local function object(ply)
  local t = trace(ply)
  if IsValid(t.Entity) and not t.Entity:IsPlayer() then
    local phys = t.Entity:GetPhysicsObject()
    if IsValid(phys) then return t.Entity, phys end
  end
end
local function firstPlayer()
  local ply = player.GetAll()[1]
  return IsValid(ply) and ply or nil
end

concommand.Add('gb_spawn', function(ply, _, args)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local model = args[1] or 'models/props_c17/oildrum001.mdl'
  if not model:match('^models/[%w_/%-%.]+%.mdl$') or model:find('..', 1, true) then error('Invalid model path') end
  local t = trace(ply)
  local pos = (t.Hit and (t.HitPos + t.HitNormal * 32)) or (ply:EyePos() + ply:GetAimVector() * 160)
  local ent = ents.Create('prop_physics')
  if not IsValid(ent) then error('prop_physics creation failed') end
  ent:SetModel(model)
  ent:SetPos(pos)
  ent:Spawn()
  undo.Create('Prop'); undo.AddEntity(ent); undo.SetPlayer(ply); undo.Finish()
  print('[gmod-sandbox] spawned', model, ent:EntIndex())
end)

concommand.Add('gb_spawnnpc', function(ply, _, args)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local class = args[1] or 'npc_combine_s'
  if not class:match('^npc_[%w_]+$') then error('Invalid NPC class') end
  local t = trace(ply)
  local pos = (t.Hit and (t.HitPos + t.HitNormal * 32)) or (ply:EyePos() + ply:GetAimVector() * 160)
  local ent = ents.Create(class)
  if not IsValid(ent) then error('NPC creation failed') end
  ent:SetPos(pos)
  ent:Spawn()
  print('[gmod-sandbox] spawned NPC', class, ent:EntIndex())
end)

concommand.Add('gb_grab', function(ply)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local id = ply:EntIndex()
  if held[id] then held[id] = nil; return end
  local ent, phys = object(ply)
  if not ent then return end
  phys:EnableMotion(true)
  phys:Wake()
  held[id] = { player = ply, entity = ent, phys = phys,
    distance = math.max(64, math.min(512, (phys:GetPos() - ply:EyePos()):Length())) }
  print('[gmod-sandbox] grabbed', ent:EntIndex())
end)

concommand.Add('gb_freeze', function(ply)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local id = ply:EntIndex()
  local phys = held[id] and held[id].phys or select(2, object(ply))
  if IsValid(phys) then phys:EnableMotion(false); held[id] = nil; print('[gmod-sandbox] frozen') end
end)

concommand.Add('gb_weld', function(ply)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local ent = object(ply)
  if not ent then return end
  local id = ply:EntIndex()
  if not IsValid(selected[id]) then selected[id] = ent; print('[gmod-sandbox] first weld prop selected'); return end
  if selected[id] == ent then selected[id] = nil; return end
  local joint = constraint.Weld(selected[id], ent, 0, 0, 0, true)
  selected[id] = nil
  if IsValid(joint) then
    undo.Create('Weld'); undo.AddEntity(joint); undo.SetPlayer(ply); undo.Finish()
    print('[gmod-sandbox] welded')
  end
end)

concommand.Add('gb_remove', function(ply)
  ply = ply or firstPlayer()
  if not IsValid(ply) then return end
  local ent = object(ply)
  if ent then ent:Remove(); print('[gmod-sandbox] removed') end
end)

concommand.Add('gb_undo', function(ply)
  ply = ply or firstPlayer()
  print('[gmod-sandbox] undo', undo.Undo(ply))
end)

concommand.Add('gb_hooks', function()
  local hooks = hook and hook.GetTable() or {}
  local names = {}
  for name, list in pairs(hooks) do
    local owners = {}
    for owner in pairs(list) do owners[#owners + 1] = tostring(owner) end
    table.sort(owners)
    names[#names + 1] = name .. '[' .. table.concat(owners, ',') .. ']'
  end
  table.sort(names)
  print('[gmod-sandbox] hooks: ' .. table.concat(names, ' '))
  if GameBoxHookStats then print('[gmod-sandbox] dispatch counts: ' .. GameBoxHookStats()) end
end)

concommand.Add('gb_status', function()
  local hooks = hook and hook.GetTable() or {}
  local names = {}
  for name, list in pairs(hooks) do
    local n = 0
    for _ in pairs(list) do n = n + 1 end
    names[#names + 1] = name .. ':' .. n
  end
  table.sort(names)
  print('[gmod-sandbox] status hooks=' .. table.concat(names, ',') ..
    ' addons=' .. addonsLoaded .. '/' .. (addonsLoaded + addonsFailed) ..
    ' gamemode=' .. tostring(gamemodeLoaded) .. ' failed=' .. #failed)
end)

-- Physgun-style hold update (same shape as ports/gmod/compat/preview.lua).
hook.Add('Think', 'gamebox-sandbox-grab', function()
  for id, item in pairs(held) do
    if not IsValid(item.player) or not IsValid(item.entity) or not IsValid(item.phys) then
      held[id] = nil
    else
      item.phys:Wake()
      item.phys:ComputeShadowControl({
        pos = item.player:EyePos() + item.player:GetAimVector() * item.distance,
        secondstoarrive = 0.1, maxspeed = 1000, maxspeeddamp = 10000,
        maxangular = 1000, maxangulardamp = 10000, dampfactor = 0.8,
        teleportdistance = 0, deltatime = 1 / 66,
      })
    end
  end
end)

-- Frame driver, called by the plugin's GameFrame: timers, hook.Run('Think'), and
-- the registered gamemode's Think function.
local frame = 0
function GameBoxFrame()
  frame = frame + 1
  local pok, perr = pcall(pollPlayer)
  if not pok then msg('player poll error', tostring(perr)) end
  local ok, err = pcall(timer._tick)
  if not ok then msg('timer error', tostring(err)) end
  if hook then
    local ok2, err2 = pcall(hook.Run, 'Think')
    if not ok2 then msg('Think error', tostring(err2)) end
  end
  if gamemode and gamemodeLoaded then pcall(gamemode.Call, 'Think') end
  if frame % 600 == 0 then
    local hooks = hook and hook.GetTable() or {}
    local think = hooks.Think
    local n = 0
    for _ in pairs(think or {}) do n = n + 1 end
    print('[gmod-sandbox] frame=' .. frame .. ' thinkHooks=' .. n ..
      ' addons=' .. addonsLoaded .. '/' .. (addonsLoaded + addonsFailed))
  end
end

-- Map lifecycle hooks the engine fires once the map's entities exist.
timer.Simple(2, function()
  if hook then hook.Run('Initialize') end
  timer.Simple(1, function()
    if hook then hook.Run('InitPostEntity') end
    msg('map lifecycle hooks dispatched (Initialize, InitPostEntity)')
  end)
end)

-- Player lifecycle: poll from the frame driver (safe context), dispatch the GMod hooks once.
local playerSeen, playerHooksDone = false, false
function pollPlayer()
  if playerHooksDone then return end
  local ply = firstPlayer()
  if not IsValid(ply) then return end
  if not playerSeen then
    playerSeen = true
    msg('player entity detected', tostring(ply:EntIndex()), tostring(ply:GetClass()))
    if hook then hook.Run('PlayerInitialSpawn', ply) end
    return
  end
  playerHooksDone = true
  if hook then hook.Run('PlayerSpawn', ply) end
  msg('player hooks dispatched (PlayerInitialSpawn, PlayerSpawn)')
end

-- Default sandbox demo: a few Lua-spawned physics props next to the player, so the
-- packed player shows real VPhysics objects without input. Removable with
-- `+gb_no_demo 1` or by removing this timer.
timer.Simple(4, function()
  local ply = firstPlayer()
  if not IsValid(ply) then ply = nil end
  for i = 1, 4 do
    local ent = ents.Create('prop_physics')
    if IsValid(ent) then
      ent:SetModel(i % 2 == 0 and 'models/props_c17/oildrum001.mdl' or 'models/props_junk/wood_crate001a.mdl')
      local offset = Vector(60 + i * 45, -120 + i * 70, 40)
      ent:SetPos((ply and ply:GetPos() or Vector(0, 0, 64)) + offset)
      ent:Spawn()
      print('[gmod-sandbox] demo prop', i, ent:EntIndex())
    end
  end
end)

msg('READY loaded=' .. #loaded .. ' failed=' .. #failed ..
  ' addons=' .. addonsLoaded .. '/' .. (addonsLoaded + addonsFailed) ..
  ' gamemode=' .. tostring(gamemodeLoaded))
print('[gmod-sandbox] controls: gb_spawn gb_spawnnpc gb_grab gb_freeze gb_weld gb_remove gb_undo gb_status')
