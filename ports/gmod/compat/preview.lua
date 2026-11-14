-- Development sandbox on real Source entities. This is not the retail GMod
-- sandbox gamemode or its spawn menu; these commands exercise the native bridge.
local hook=require("hook")
local held,selected={},{}
local function trace(ply)
  local start=ply:EyePos()
  return util.TraceLine({start=start,endpos=start+ply:GetAimVector()*1500,filter=ply})
end
local function object(ply)
  local t=trace(ply)
  if IsValid(t.Entity) and !t.Entity:IsPlayer() then
    local phys=t.Entity:GetPhysicsObject()
    if IsValid(phys) then return t.Entity,phys end
  end
end
concommand.Add("gb_spawn",function(ply,_,args)
  local model=args[1] or "models/props_c17/oildrum001.mdl"
  if !model:match("^models/[%w_/%-%.]+%.mdl$") or model:find("..",1,true) then error("Invalid model path") end
  local t=trace(ply)
  local pos=t.Hit and t.HitPos+t.HitNormal*32 or ply:EyePos()+ply:GetAimVector()*160
  local ent=ents.Create("prop_physics")
  if !IsValid(ent) then error("prop_physics creation failed") end
  ent:SetModel(model);ent:SetPos(pos);ent:Spawn()
  undo.Create("Prop");undo.AddEntity(ent);undo.SetPlayer(ply);undo.Finish()
  print("[gmod-preview] spawned",model,ent:EntIndex())
end)
concommand.Add("gb_grab",function(ply)
  local id=ply:EntIndex()
  if held[id] then held[id]=nil;return end
  local ent,phys=object(ply)
  if !ent then return end
  phys:EnableMotion(true);phys:Wake()
  held[id]={player=ply,entity=ent,phys=phys,distance=math.max(64,math.min(512,(phys:GetPos()-ply:EyePos()):Length()))}
  print("[gmod-preview] grabbed",ent:EntIndex())
end)
concommand.Add("gb_freeze",function(ply)
  local id=ply:EntIndex()
  local phys=held[id] and held[id].phys or select(2,object(ply))
  if IsValid(phys) then phys:EnableMotion(false);held[id]=nil;print("[gmod-preview] frozen") end
end)
concommand.Add("gb_weld",function(ply)
  local ent=object(ply);if !ent then return end
  local id=ply:EntIndex()
  if !IsValid(selected[id]) then selected[id]=ent;print("[gmod-preview] first weld prop selected");return end
  if selected[id]==ent then selected[id]=nil;return end
  local joint=constraint.Weld(selected[id],ent,0,0,0,true);selected[id]=nil
  if IsValid(joint) then undo.Create("Weld");undo.AddEntity(joint);undo.SetPlayer(ply);undo.Finish();print("[gmod-preview] welded") end
end)
concommand.Add("gb_remove",function(ply)
  local ent=object(ply);if ent then ent:Remove();print("[gmod-preview] removed") end
end)
concommand.Add("gb_undo",function(ply) print("[gmod-preview] undo",undo.Undo(ply)) end)
hook.Add("Think","gamebox-preview-grab",function()
  for id,item in pairs(held) do
    if !IsValid(item.player) or !IsValid(item.entity) or !IsValid(item.phys) then held[id]=nil;continue end
    item.phys:Wake()
    item.phys:ComputeShadowControl({pos=item.player:EyePos()+item.player:GetAimVector()*item.distance,
      secondstoarrive=0.1,maxspeed=1000,maxspeeddamp=10000,maxangular=1000,maxangulardamp=10000,dampfactor=0.8,teleportdistance=0,deltatime=1/66})
  end
end)
print("[gmod-preview] READY: Q spawn; F grab/drop; R freeze; C select/weld; Z undo; X remove")
