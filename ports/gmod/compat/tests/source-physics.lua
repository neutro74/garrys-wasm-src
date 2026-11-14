-- Live integration test: every entity/physics call below reaches Source/VPhysics.
local hook=require("hook")
local starts=ents.FindByClass("info_player_start")
local base=IsValid(starts[1]) and starts[1]:GetPos() or Vector(0,0,0)
local ground=util.TraceLine({start=base+Vector(0,0,32),endpos=base-Vector(0,0,100)})
assert(ground.Hit,"Source world trace did not hit the ground")
local barrel=ents.Create("prop_physics")
assert(IsValid(barrel),"ents.Create failed")
barrel:SetModel("models/props_c17/oildrum001.mdl")
barrel:SetPos(base+Vector(80,0,160))
barrel:Spawn()
local phys=barrel:GetPhysicsObject()
assert(IsValid(phys) and phys:GetMass()>0,"missing VPhysics object")
phys:EnableMotion(false)
local initial=phys:GetPos()
local other=ents.Create("prop_physics")
other:SetModel("models/props_c17/oildrum001.mdl")
other:SetPos(initial+Vector(60,0,0));other:Spawn()
local otherPhys=other:GetPhysicsObject()
local weld=constraint.Weld(barrel,other,0,0,0,true)
assert(IsValid(weld),"native weld failed")
local otherStart=otherPhys:GetPos()
local started,stage,checkpoint,removed
print("[gmod-test] SPAWN",barrel:EntIndex(),phys:GetMass(),initial.x,initial.y,initial.z)
hook.Add("Think","gamebox-physics-test",function()
  started=started or CurTime()
  local age=CurTime()-started
  if !stage and age>1 then
    assert((phys:GetPos()-initial):Length()<0.1,"frozen prop moved")
    assert((otherPhys:GetPos()-otherStart):Length()<1,"welded prop fell away")
    weld:Remove();assert(!IsValid(weld),"removed weld stayed valid")
    print("[gmod-test] WELD: fixed joint held a moving prop; removal invalidated it")
    phys:EnableMotion(true);phys:Wake();stage=1
  elseif stage==1 and age>1.4 then
    local pos=phys:GetPos()
    assert(pos.z<initial.z-1,"gravity did not move the prop")
    phys:EnableMotion(false);checkpoint=pos;stage=2
    print("[gmod-test] GRAVITY",initial.z,pos.z)
  elseif stage==2 and age>2 then
    assert((phys:GetPos()-checkpoint):Length()<0.1,"refrozen prop moved")
    assert(!phys:IsMotionEnabled(),"freeze failed")
    undo.Create("Physics test");undo.AddEntity(barrel);undo.AddEntity(other)
    assert(undo.Finish(),"undo did not record entities")
    assert(undo.Undo()==2,"undo did not remove both real props");stage=3
  elseif stage==3 and age>2.2 then
    assert(!IsValid(barrel) and !IsValid(phys),"stale handles stayed valid")
    hook.Remove("Think","gamebox-physics-test")
    print("[gmod-test] PASS: trace, spawn, gravity, freeze, weld, undo, stale handles")
  end
end)
