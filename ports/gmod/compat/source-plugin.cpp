// Engine-side compatibility bridge. Built against this checkout's HL2 headers;
// loaded via Source's server plugin API, without replacing working game DLLs.
#include "cbase.h"
#include "engine/iserverplugin.h"
#include "toolframework/itoolentity.h"
#include "vphysics_interface.h"
#include "vphysics/constraints.h"
#include "engine/ICollideable.h"
#include "engine/IEngineTrace.h"
#include "player.h"
extern "C" {
#include "lua.h"
#include "lauxlib.h"
int gb_open(int,const char *);
void gb_close(void);
int gb_run(const char *,const char *);
int gb_file(const char *);
const char *gb_error(void);
void gb_tick(double);
lua_State *gb_state(void);
int gb_pcall(int,int);
}

static IVEngineServer *host;
static IServerTools *tools;
static IPhysics *physics_api;
static IEngineTrace *tracer;
static bool active;
static const char *ENTITY="GameBox.Entity", *PHYSICS="GameBox.Physics", *VEC="GameBox.Vector", *WELD="GameBox.Weld";
struct WeldRecord {IPhysicsConstraint *joint;IPhysicsEnvironment *env;unsigned int first,second;bool nocollide;};
static WeldRecord welds[1024];
static int weld_count;
static void pair_collisions(CBaseEntity *a,CBaseEntity *b,bool enable) {
  // Use Source's native collision-pair entity through its public virtual API.
  // Hidden Phys* helpers are not exported by the existing HL2 WASM game DLL.
  char olda[1024]={0},oldb[1024]={0},namea[64],nameb[64];
  tools->GetKeyValue(a,"targetname",olda,sizeof(olda));tools->GetKeyValue(b,"targetname",oldb,sizeof(oldb));
  snprintf(namea,sizeof(namea),"__gamebox_pair_a_%u",a->GetRefEHandle().ToInt());
  snprintf(nameb,sizeof(nameb),"__gamebox_pair_b_%u",b->GetRefEHandle().ToInt());
  tools->SetKeyValue(a,"targetname",namea);tools->SetKeyValue(b,"targetname",nameb);
  CBaseEntity *controller=tools->CreateEntityByName("logic_collision_pair");
  if(controller){
    tools->SetKeyValue(controller,"attach1",namea);tools->SetKeyValue(controller,"attach2",nameb);tools->DispatchSpawn(controller);
    variant_t value;controller->AcceptInput(enable?"EnableCollisions":"DisableCollisions",NULL,NULL,value,0);
    tools->RemoveEntity(controller);
  }
  tools->SetKeyValue(a,"targetname",olda);tools->SetKeyValue(b,"targetname",oldb);
}

static CBaseEntity *from_handle(unsigned int ref) {
  CBaseHandle h(ref);
  CBaseEntity *ent=NULL;
  if(h.GetEntryIndex()<MAX_EDICTS)ent=tools->GetBaseEntityByEntIndex(h.GetEntryIndex());
  else for(CBaseEntity *e=tools->FirstEntity();e;e=tools->NextEntity(e))if(e->GetRefEHandle().ToInt()==ref){ent=e;break;}
  return ent && ent->GetRefEHandle().ToInt()==ref?ent:NULL;
}
static void remove_weld(WeldRecord &w) {
  if(!w.joint)return;
  CBaseEntity *a=from_handle(w.first),*b=from_handle(w.second);
  w.env->DestroyConstraint(w.joint);w.joint=NULL;
  if(w.nocollide&&a&&b&&a->VPhysicsGetObject()&&b->VPhysicsGetObject()){
    bool remaining=false;
    for(int i=0;i<weld_count;i++)if(welds[i].joint&&welds[i].nocollide&&
      ((welds[i].first==w.first&&welds[i].second==w.second)||(welds[i].first==w.second&&welds[i].second==w.first)))remaining=true;
    if(!remaining)pair_collisions(a,b,true);
  }
}

static CBaseEntity *resolve(lua_State *L,int slot,const char *meta,bool strict=true) {
  unsigned int *stored=(unsigned int *)luaL_checkudata(L,slot,meta);
  CBaseEntity *ent=from_handle(*stored);
  if(ent)return ent;
  if(strict)luaL_error(L,"Tried to use an invalid Entity");
  return NULL;
}
static int push_entity(lua_State *L,CBaseEntity *ent,const char *meta=ENTITY) {
  if(!ent){lua_pushnil(L);return 1;}
  unsigned int *ref=(unsigned int *)lua_newuserdatauv(L,sizeof(unsigned int),0);
  *ref=ent->GetRefEHandle().ToInt();luaL_setmetatable(L,meta);return 1;
}
static Vector &vec(lua_State *L,int i) {return *(Vector *)luaL_checkudata(L,i,VEC);}
static int push_vec(lua_State *L,const Vector &v) {
  Vector *out=(Vector *)lua_newuserdatauv(L,sizeof(Vector),0);
  out->x=v.x;out->y=v.y;out->z=v.z;luaL_setmetatable(L,VEC);return 1;
}
static int new_vec(lua_State *L) {return push_vec(L,Vector(luaL_optnumber(L,1,0),luaL_optnumber(L,2,0),luaL_optnumber(L,3,0)));}
static int vector_index(lua_State *L) {
  const char *key=luaL_checkstring(L,2);Vector &v=vec(L,1);
  if(!strcmp(key,"x"))lua_pushnumber(L,v.x);
  else if(!strcmp(key,"y"))lua_pushnumber(L,v.y);
  else if(!strcmp(key,"z"))lua_pushnumber(L,v.z);
  else {luaL_getmetatable(L,VEC);lua_pushvalue(L,2);lua_rawget(L,-2);}
  return 1;
}
static int vector_set(lua_State *L) {
  Vector &v=vec(L,1);const char *key=luaL_checkstring(L,2);float value=luaL_checknumber(L,3);
  if(!strcmp(key,"x"))v.x=value;else if(!strcmp(key,"y"))v.y=value;else if(!strcmp(key,"z"))v.z=value;
  else return luaL_error(L,"Invalid Vector component");return 0;
}
static int vector_add(lua_State *L) {return push_vec(L,vec(L,1)+vec(L,2));}
static int vector_sub(lua_State *L) {return push_vec(L,vec(L,1)-vec(L,2));}
static int vector_mul(lua_State *L) {
  if(lua_isnumber(L,1))return push_vec(L,vec(L,2)*luaL_checknumber(L,1));
  return push_vec(L,vec(L,1)*luaL_checknumber(L,2));
}
static int vector_length(lua_State *L) {lua_pushnumber(L,vec(L,1).Length());return 1;}
static int vector_normal(lua_State *L) {Vector v=vec(L,1);float length=v.Length();return push_vec(L,length>0?v/length:Vector(0,0,0));}
static int is_vector(lua_State *L) {lua_pushboolean(L,luaL_testudata(L,1,VEC)!=NULL);return 1;}
static int ent_valid(lua_State *L) {lua_pushboolean(L,resolve(L,1,ENTITY,false)!=NULL);return 1;}
static int ent_index(lua_State *L) {lua_pushinteger(L,resolve(L,1,ENTITY)->GetRefEHandle().GetEntryIndex());return 1;}
static int ent_class(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);
  auto *networkable=ent->GetNetworkable();
  if(!networkable){lua_pushstring(L,"");return 1;}
  lua_pushstring(L,networkable->GetClassName());return 1;
}
static int ent_isplayer(lua_State *L) {lua_pushboolean(L,resolve(L,1,ENTITY)->IsPlayer());return 1;}
static int ent_eyes(lua_State *L) {return push_vec(L,resolve(L,1,ENTITY)->EyePosition());}
static int ent_aim(lua_State *L) {
  const QAngle &angle=resolve(L,1,ENTITY)->EyeAngles();
  float p=angle.x*(3.14159265358979323846f/180),y=angle.y*(3.14159265358979323846f/180);
  return push_vec(L,Vector(cosf(p)*cosf(y),cosf(p)*sinf(y),-sinf(p)));
}
static int ent_key(lua_State *L) {
  CBaseEntity *e=resolve(L,1,ENTITY);if(!e->IsPlayer())return luaL_error(L,"KeyDown requires a Player");
  lua_pushboolean(L,(((CBasePlayer *)e)->m_nButtons & (int)luaL_checkinteger(L,2))!=0);return 1;
}
// Per-entity Lua tables (GMod's Entity:GetTable/SetTable), kept in the Lua registry.
static int ent_table(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);
  unsigned int ref=ent->GetRefEHandle().ToInt();
  lua_pushliteral(L,"GameBox.EntityTables");lua_rawget(L,LUA_REGISTRYINDEX);
  lua_pushinteger(L,(lua_Integer)ref);lua_rawget(L,-2);
  if(lua_isnil(L,-1)) {
    lua_pop(L,1);lua_newtable(L);
    lua_pushinteger(L,(lua_Integer)ref);lua_pushvalue(L,-2);lua_rawset(L,-4);
  }
  return 1;
}
static int ent_settable(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);
  luaL_checktype(L,2,LUA_TTABLE);
  unsigned int ref=ent->GetRefEHandle().ToInt();
  lua_pushliteral(L,"GameBox.EntityTables");lua_rawget(L,LUA_REGISTRYINDEX);
  lua_pushinteger(L,(lua_Integer)ref);lua_pushvalue(L,2);lua_rawset(L,-3);
  return 0;
}
static int ent_velocity(lua_State *L) {return push_vec(L,resolve(L,1,ENTITY)->GetAbsVelocity());}
static int ent_forward(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);Vector forward;
  AngleVectors(ent->GetAbsAngles(),&forward);return push_vec(L,forward);
}
static int ent_health(lua_State *L) {lua_pushinteger(L,resolve(L,1,ENTITY)->GetHealth());return 1;}
static int find_meta_table(lua_State *L) {
  const char *name=luaL_checkstring(L,1);
  if(!strcmp(name,"Player")||!strcmp(name,"Entity")||!strcmp(name,"NextBot")||
     !strcmp(name,"Vehicle")||!strcmp(name,"Weapon")||!strcmp(name,"NPC")) {
    luaL_getmetatable(L,ENTITY);return 1;
  }
  if(!strcmp(name,"PhysicsObject")||!strcmp(name,"PhysObj")) {luaL_getmetatable(L,PHYSICS);return 1;}
  if(!strcmp(name,"Vector")) {luaL_getmetatable(L,VEC);return 1;}
  // Unknown meta tables stay plain Lua tables so addon extension files load.
  lua_pushliteral(L,"GameBox.MetaTables");lua_rawget(L,LUA_REGISTRYINDEX);
  lua_pushstring(L,name);lua_rawget(L,-2);
  if(lua_isnil(L,-1)) {lua_pop(L,1);lua_newtable(L);lua_pushstring(L,name);lua_pushvalue(L,-2);lua_rawset(L,-4);}
  return 1;
}
static int ent_equal(lua_State *L) {
  unsigned int *a=(unsigned int *)luaL_checkudata(L,1,ENTITY),*b=(unsigned int *)luaL_testudata(L,2,ENTITY);
  lua_pushboolean(L,b&&*a==*b);return 1;
}
static int ent_model(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);const char *model=luaL_checkstring(L,2);
  host->PrecacheModel(model,true);tools->SetKeyValue(ent,"model",model);return 0;
}
static int ent_spawn(lua_State *L) {tools->DispatchSpawn(resolve(L,1,ENTITY));return 0;}
static int ent_position(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);const Vector &pos=vec(L,2);
  IPhysicsObject *physics=ent->VPhysicsGetObject();
  if(physics){Vector old;QAngle angles;physics->GetPosition(&old,&angles);physics->SetPosition(pos,angles,true);physics->Wake();}
  else tools->SetKeyValue(ent,"origin",pos);
  return 0;
}
static int ent_get_position(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);
  if(!ent->GetCollideable())return push_vec(L,Vector(0,0,0));
  return push_vec(L,ent->GetCollideable()->GetCollisionOrigin());
}
static int ent_remove(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);unsigned int ref=ent->GetRefEHandle().ToInt();
  for(int i=0;i<weld_count;i++)if(welds[i].first==ref||welds[i].second==ref)remove_weld(welds[i]);
  tools->RemoveEntity(ent);return 0;
}
static int ent_physics(lua_State *L) {
  CBaseEntity *ent=resolve(L,1,ENTITY);
  return push_entity(L,ent->VPhysicsGetObject()?ent:NULL,PHYSICS);
}
static IPhysicsObject *get_physics(lua_State *L) {
  IPhysicsObject *p=resolve(L,1,PHYSICS)->VPhysicsGetObject();
  if(!p)luaL_error(L,"Tried to use an invalid PhysicsObject");return p;
}
static int phys_valid(lua_State *L) {CBaseEntity *e=resolve(L,1,PHYSICS,false);lua_pushboolean(L,e&&e->VPhysicsGetObject());return 1;}
static int phys_motion(lua_State *L) {get_physics(L)->EnableMotion(lua_toboolean(L,2));return 0;}
static int phys_is_motion(lua_State *L) {lua_pushboolean(L,get_physics(L)->IsMotionEnabled());return 1;}
static int phys_mass(lua_State *L) {lua_pushnumber(L,get_physics(L)->GetMass());return 1;}
static int phys_wake(lua_State *L) {get_physics(L)->Wake();return 0;}
static int phys_pos(lua_State *L) {Vector pos;QAngle angles;get_physics(L)->GetPosition(&pos,&angles);return push_vec(L,pos);}
static int phys_setpos(lua_State *L) {IPhysicsObject *p=get_physics(L);Vector old;QAngle angle;p->GetPosition(&old,&angle);p->SetPosition(vec(L,2),angle,true);return 0;}
static double number_field(lua_State *L,int table,const char *key,double value) {
  lua_getfield(L,table,key);double out=lua_isnil(L,-1)?value:luaL_checknumber(L,-1);lua_pop(L,1);return out;
}
static int phys_shadow(lua_State *L) {
  IPhysicsObject *p=get_physics(L);luaL_checktype(L,2,LUA_TTABLE);
  hlshadowcontrol_params_t params;
  lua_getfield(L,2,"pos");params.targetPosition=vec(L,-1);lua_pop(L,1);
  Vector old;p->GetPosition(&old,&params.targetRotation);
  params.maxAngular=number_field(L,2,"maxangular",1000);params.maxDampAngular=number_field(L,2,"maxangulardamp",10000);
  params.maxSpeed=number_field(L,2,"maxspeed",1000);params.maxDampSpeed=number_field(L,2,"maxspeeddamp",10000);
  params.dampFactor=number_field(L,2,"dampfactor",0.8);params.teleportDistance=number_field(L,2,"teleportdistance",0);
  lua_pushnumber(L,p->ComputeShadowControl(params,number_field(L,2,"secondstoarrive",0.1),number_field(L,2,"deltatime",1.0/66)));return 1;
}
static int create_entity(lua_State *L) {return push_entity(L,tools->CreateEntityByName(luaL_checkstring(L,1)));}
static int all_entities(lua_State *L) {
  lua_newtable(L);int i=0;
  for(CBaseEntity *e=tools->FirstEntity();e;e=tools->NextEntity(e)){push_entity(L,e);lua_rawseti(L,-2,++i);}
  return 1;
}
static int find_class(lua_State *L) {
  const char *name=luaL_checkstring(L,1);lua_newtable(L);int i=0;CBaseEntity *e=NULL;
  while((e=tools->FindEntityByClassname(e,name))!=NULL){push_entity(L,e);lua_rawseti(L,-2,++i);}return 1;
}
static int find_in_sphere(lua_State *L) {
  const Vector &center=vec(L,1);float radius=(float)luaL_checknumber(L,2);
  lua_newtable(L);int i=0;
  for(CBaseEntity *e=tools->FirstEntity();e;e=tools->NextEntity(e)) {
    if(!e->GetCollideable())continue;
    if((e->GetCollideable()->GetCollisionOrigin()-center).LengthSqr()<=radius*radius) {
      push_entity(L,e);lua_rawseti(L,-2,++i);
    }
  }
  return 1;
}
static int all_players(lua_State *L) {
  lua_newtable(L);int i=0;
  for(CBaseEntity *e=tools->FirstEntity();e;e=tools->NextEntity(e))if(e->IsPlayer()){push_entity(L,e);lua_rawseti(L,-2,++i);}return 1;
}
class LuaTraceFilter : public ITraceFilter {
 public:
  CBaseEntity *skip;
  bool ShouldHitEntity(IHandleEntity *entity,int) override {return (IHandleEntity *)skip!=entity;}
  TraceType_t GetTraceType() const override {return TRACE_EVERYTHING;}
};
static void push_trace(lua_State *L,const trace_t &trace) {
  lua_newtable(L);
  lua_pushboolean(L,trace.DidHit());lua_setfield(L,-2,"Hit");
  lua_pushboolean(L,trace.m_pEnt&&trace.m_pEnt->GetRefEHandle().GetEntryIndex()==0);lua_setfield(L,-2,"HitWorld");
  lua_pushnumber(L,trace.fraction);lua_setfield(L,-2,"Fraction");
  lua_pushboolean(L,trace.startsolid);lua_setfield(L,-2,"StartSolid");
  push_vec(L,trace.endpos);lua_setfield(L,-2,"HitPos");push_vec(L,trace.plane.normal);lua_setfield(L,-2,"HitNormal");
  push_entity(L,trace.m_pEnt);lua_setfield(L,-2,"Entity");
}
static int trace_line(lua_State *L) {
  if(!tracer)return luaL_error(L,"Source trace interface unavailable");
  luaL_checktype(L,1,LUA_TTABLE);
  lua_getfield(L,1,"start");Vector start=vec(L,-1);lua_pop(L,1);
  lua_getfield(L,1,"endpos");Vector end=vec(L,-1);lua_pop(L,1);
  LuaTraceFilter filter;filter.skip=NULL;
  lua_getfield(L,1,"filter");if(!lua_isnil(L,-1))filter.skip=resolve(L,-1,ENTITY,false);lua_pop(L,1);
  unsigned int mask=(unsigned int)number_field(L,1,"mask",MASK_SOLID);
  Ray_t ray;ray.Init(start,end);trace_t trace;tracer->TraceRay(ray,mask,&filter,&trace);
  push_trace(L,trace);return 1;
}
static int trace_hull(lua_State *L) {
  if(!tracer)return luaL_error(L,"Source trace interface unavailable");
  luaL_checktype(L,1,LUA_TTABLE);
  lua_getfield(L,1,"start");Vector start=vec(L,-1);lua_pop(L,1);
  lua_getfield(L,1,"endpos");Vector end=vec(L,-1);lua_pop(L,1);
  lua_getfield(L,1,"mins");Vector mins=vec(L,-1);lua_pop(L,1);
  lua_getfield(L,1,"maxs");Vector maxs=vec(L,-1);lua_pop(L,1);
  LuaTraceFilter filter;filter.skip=NULL;
  lua_getfield(L,1,"filter");if(!lua_isnil(L,-1))filter.skip=resolve(L,-1,ENTITY,false);lua_pop(L,1);
  unsigned int mask=(unsigned int)number_field(L,1,"mask",MASK_SOLID);
  Ray_t ray;ray.Init(start,end,mins,maxs);trace_t trace;tracer->TraceRay(ray,mask,&filter,&trace);
  push_trace(L,trace);return 1;
}
static int world(lua_State *L) {return push_entity(L,tools->GetBaseEntityByEntIndex(0));}
static int create_weld(lua_State *L) {
  CBaseEntity *a=resolve(L,1,ENTITY),*b=resolve(L,2,ENTITY);
  if(luaL_optinteger(L,3,0)!=0||luaL_optinteger(L,4,0)!=0)return luaL_error(L,"Weld: nonzero physics bones are not implemented yet");
  if(luaL_optnumber(L,5,0)!=0||lua_toboolean(L,7))return luaL_error(L,"Weld: breakable joints are not implemented yet");
  IPhysicsObject *p=a->VPhysicsGetObject(),*q=b->VPhysicsGetObject();
  if(!p||!q||p==q){lua_pushnil(L);return 1;}
  if(weld_count>=1024)return luaL_error(L,"Weld count limit reached");
  IPhysicsEnvironment *env=physics_api?physics_api->GetActiveEnvironmentByIndex(0):NULL;
  if(!env)return luaL_error(L,"Source physics environment unavailable");
  constraint_fixedparams_t params;params.constraint.Defaults();
  matrix3x4_t ref,attached;p->GetPositionMatrix(&ref);q->GetPositionMatrix(&attached);
  for(int i=0;i<3;i++){
    for(int j=0;j<3;j++){params.attachedRefXform[i][j]=0;for(int k=0;k<3;k++)params.attachedRefXform[i][j]+=ref[k][i]*attached[k][j];}
    params.attachedRefXform[i][3]=0;for(int k=0;k<3;k++)params.attachedRefXform[i][3]+=ref[k][i]*(attached[k][3]-ref[k][3]);
  }
  IPhysicsConstraint *joint=env->CreateFixedConstraint(p,q,NULL,params);
  if(!joint){lua_pushnil(L);return 1;}
  WeldRecord &record=welds[weld_count];record.joint=joint;record.env=env;
  record.first=a->GetRefEHandle().ToInt();record.second=b->GetRefEHandle().ToInt();record.nocollide=lua_toboolean(L,6);
  if(record.nocollide)pair_collisions(a,b,false);
  int *id=(int *)lua_newuserdatauv(L,sizeof(int),0);*id=weld_count++;luaL_setmetatable(L,WELD);return 1;
}
static int weld_valid(lua_State *L) {
  int i=*(int *)luaL_checkudata(L,1,WELD);
  lua_pushboolean(L,i>=0&&i<weld_count&&welds[i].joint&&from_handle(welds[i].first)&&from_handle(welds[i].second));return 1;
}
static int weld_remove(lua_State *L) {
  int i=*(int *)luaL_checkudata(L,1,WELD);if(i>=0&&i<weld_count)remove_weld(welds[i]);return 0;
}
static void metatable(lua_State *L,const char *name,const luaL_Reg *methods) {
  luaL_newmetatable(L,name);luaL_setfuncs(L,methods,0);lua_pushvalue(L,-1);lua_setfield(L,-2,"__index");lua_pop(L,1);
}
static void bind_source() {
  lua_State *L=gb_state();
  const luaL_Reg em[]={ {"IsValid",ent_valid},{"EntIndex",ent_index},{"GetClass",ent_class},
    {"IsPlayer",ent_isplayer},{"EyePos",ent_eyes},{"GetAimVector",ent_aim},{"KeyDown",ent_key},{"__eq",ent_equal},
    {"SetModel",ent_model},{"Spawn",ent_spawn},{"SetPos",ent_position},{"GetPos",ent_get_position},
    {"Remove",ent_remove},{"GetPhysicsObject",ent_physics},
    {"GetTable",ent_table},{"SetTable",ent_settable},{"GetVelocity",ent_velocity},
    {"GetForward",ent_forward},{"Health",ent_health},{NULL,NULL} };
  const luaL_Reg pm[]={ {"IsValid",phys_valid},{"EnableMotion",phys_motion},{"IsMotionEnabled",phys_is_motion},
    {"GetMass",phys_mass},{"Wake",phys_wake},{"GetPos",phys_pos},{"SetPos",phys_setpos},{"ComputeShadowControl",phys_shadow},{NULL,NULL} };
  metatable(L,ENTITY,em);metatable(L,PHYSICS,pm);
  const luaL_Reg wm[]={{"IsValid",weld_valid},{"Remove",weld_remove},{NULL,NULL}};metatable(L,WELD,wm);
  const luaL_Reg vectors[]={ {"__index",vector_index},{"__newindex",vector_set},{"__add",vector_add},
    {"__sub",vector_sub},{"__mul",vector_mul},{"Length",vector_length},{"GetNormalized",vector_normal},{NULL,NULL} };
  luaL_newmetatable(L,VEC);luaL_setfuncs(L,vectors,0);lua_pop(L,1);
  lua_pushcfunction(L,new_vec);lua_setglobal(L,"Vector");lua_pushcfunction(L,is_vector);lua_setglobal(L,"isvector");
  const luaL_Reg entities[]={ {"Create",create_entity},{"GetAll",all_entities},{"FindByClass",find_class},
    {"FindInSphere",find_in_sphere},{NULL,NULL} };
  luaL_newlib(L,entities);lua_setglobal(L,"ents");
  lua_newtable(L);lua_pushcfunction(L,world);lua_setfield(L,-2,"GetWorld");lua_setglobal(L,"game");
  lua_newtable(L);lua_pushcfunction(L,create_weld);lua_setfield(L,-2,"Weld");lua_setglobal(L,"constraint");
  lua_newtable(L);lua_pushcfunction(L,trace_line);lua_setfield(L,-2,"TraceLine");
  lua_pushcfunction(L,trace_hull);lua_setfield(L,-2,"TraceHull");lua_setglobal(L,"util");
  lua_pushcfunction(L,find_meta_table);lua_setglobal(L,"FindMetaTable");
  lua_newtable(L);lua_pushcfunction(L,all_players);lua_setfield(L,-2,"GetAll");lua_setglobal(L,"player");
}
static void run(const char *text) {if(!gb_run(text,"=Source")){Msg("[gmod-error] %s\n",gb_error());active=false;}}

class GameBoxGMod : public IServerPluginCallbacks {
 public:
  bool Load(CreateInterfaceFn engineFactory,CreateInterfaceFn gameFactory) override {
    host=(IVEngineServer *)engineFactory(INTERFACEVERSION_VENGINESERVER,NULL);
    tools=(IServerTools *)gameFactory(VSERVERTOOLS_INTERFACE_VERSION,NULL);
    physics_api=(IPhysics *)engineFactory(VPHYSICS_INTERFACE_VERSION,NULL);
    tracer=(IEngineTrace *)engineFactory(INTERFACEVERSION_ENGINETRACE_SERVER,NULL);
    if(!host||!tools)return false;
    host->LogPrint("[gmod] Source compatibility plugin loaded\n");Msg("[gmod] Source compatibility plugin loaded\n");return true;
  }
  void Unload() override {for(int i=0;i<weld_count;i++)remove_weld(welds[i]);weld_count=0;gb_close();active=false;}
  void Pause() override {}
  void UnPause() override {}
  const char *GetPluginDescription() override {return "GameBox GLua / Source compatibility bridge";}
  void LevelInit(const char *) override {active=false;}
  void ServerActivate(edict_t *,int,int) override {
    if(!gb_open(1,"/gamebox-gmod/lua"))return;
    if(!gb_file("bootstrap.lua")){Msg("[gmod-error] %s\n",gb_error());return;}
    weld_count=0;bind_source();active=true;
    if(!gb_file("startup.lua")){Msg("[gmod-error] %s\n",gb_error());active=false;return;}
    host->LogPrint("[gmod] Lua server activated\n");Msg("[gmod] Lua server activated\n");
  }
  void GameFrame(bool simulating) override {
    if(!active||!simulating)return;
    gb_tick(host->GetServerTime());
    lua_State *L=gb_state();int base=lua_gettop(L);
    lua_getglobal(L,"GameBoxFrame");
    if(lua_isfunction(L,-1)) {
      if(!gb_pcall(0,0))Msg("[gmod-error] %s\n",gb_error());
    } else {
      run("timer._tick(); if hook then hook.Run('Think') end");
    }
    lua_settop(L,base);
  }
  void LevelShutdown() override {for(int i=0;i<weld_count;i++)remove_weld(welds[i]);weld_count=0;active=false;gb_close();}
  void ClientActive(edict_t *) override {}
  void ClientDisconnect(edict_t *) override {}
  void ClientPutInServer(edict_t *entity,const char *) override {
    // The engine callback context is not safe for a nested Lua/engine round trip; the
    // sandbox startup polls for the player entity from GameFrame and dispatches the
    // PlayerInitialSpawn/PlayerSpawn hooks there.
    if(active&&entity)Msg("[gmod] client put in server\n");
  }
  void SetCommandClient(int) override {}
  void ClientSettingsChanged(edict_t *) override {}
  PLUGIN_RESULT ClientConnect(bool *,edict_t *,const char *,const char *,char *,int) override {return PLUGIN_CONTINUE;}
  PLUGIN_RESULT ClientCommand(edict_t *client,const CCommand &args) override {
    if(!active)return PLUGIN_CONTINUE;
    if(!strcmp(args[0],"gb_lua")){run(args.ArgS());return PLUGIN_STOP;}
    lua_State *L=gb_state();int base=lua_gettop(L);
    lua_getglobal(L,"concommand");lua_getfield(L,-1,"_callbacks");lua_getfield(L,-1,args[0]);
    if(!lua_isfunction(L,-1)){lua_settop(L,base);return PLUGIN_CONTINUE;}
    push_entity(L,client->GetUnknown()->GetBaseEntity());lua_pushstring(L,args[0]);lua_newtable(L);
    for(int i=1;i<args.ArgC();i++){lua_pushstring(L,args[i]);lua_rawseti(L,-2,i);}
    lua_pushstring(L,args.ArgS());if(!gb_pcall(4,0))Msg("[gmod-error] %s\n",gb_error());
    lua_settop(L,base);return PLUGIN_STOP;
  }
  PLUGIN_RESULT NetworkIDValidated(const char *,const char *) override {return PLUGIN_CONTINUE;}
  void OnQueryCvarValueFinished(QueryCvarCookie_t,edict_t *,EQueryCvarValueStatus,const char *,const char *) override {}
  void OnEdictAllocated(edict_t *) override {}
  void OnEdictFreed(const edict_t *) override {}
};
static GameBoxGMod plugin;
extern "C" __attribute__((visibility("default"))) void *CreateInterface(const char *name,int *code) {
  if(!strcmp(name,INTERFACEVERSION_ISERVERPLUGINCALLBACKS)){if(code)*code=0;return &plugin;}
  if(code)*code=1;return NULL;
}
