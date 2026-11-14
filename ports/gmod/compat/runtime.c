// Portable GLua runtime. No OS, native library loader or host filesystem APIs
// are exposed to Lua. Source engine bindings are a separate integration layer.
#include "lua.h"
#include "lauxlib.h"
#include "lualib.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static lua_State *vm;
static char root[1024], current[1024], error_text[4096];
static size_t allocated;
static int remaining;
static double game_time;
// For native Source bindings in the engine plugin. Never exposed to addon Lua.
lua_State *gb_state(void) {return vm;}

static void *limited_alloc(void *ud, void *ptr, size_t old, size_t size) {
  (void)ud;
  if (!ptr) old=0;
  if (!size) { free(ptr); allocated-=old; return NULL; }
  if (allocated-old+size>32*1024*1024) return NULL;
  void *result=realloc(ptr,size);
  if(result) allocated=allocated-old+size;
  return result;
}
static void budget(lua_State *L,lua_Debug *ar) {
  (void)ar;
  if(--remaining<=0) luaL_error(L,"GLua instruction budget exceeded");
}
static int path_ok(const char *p) {
  if(!*p || *p=='/' || strchr(p,'\\') || strchr(p,':')) return 0;
  const char *s=p;
  do {
    const char *end=strchr(s,'/'); size_t n=end?(size_t)(end-s):strlen(s);
    if(!n || (n==1 && s[0]=='.') || (n==2 && s[0]=='.' && s[1]=='.'))return 0;
    for(size_t i=0;i<n;i++)if((unsigned char)s[i]<32)return 0;
    if(!end)break;
    s=end+1;
  }while(1);
  return 1;
}
static int readable(const char *p) { FILE *f=fopen(p,"rb");if(!f)return 0;fclose(f);return 1; }
static int include_file(lua_State *L) {
  const char *name=luaL_checkstring(L,1);
  if(!path_ok(name))return luaL_error(L,"Invalid Lua include path: %s",name);
  char relative[1024], full[2048], previous[1024];
  snprintf(previous,sizeof(previous),"%s",current);
  snprintf(relative,sizeof(relative),"%s%s",current,name);
  if(strlen(current)+strlen(name)>=sizeof(relative))return luaL_error(L,"Lua path too long");
  snprintf(full,sizeof(full),"%s/%s",root,relative);
  if(!readable(full)) { snprintf(relative,sizeof(relative),"%s",name);snprintf(full,sizeof(full),"%s/%s",root,name); }
  int base=lua_gettop(L);
  if(luaL_loadfilex(L,full,"t")!=LUA_OK)return lua_error(L);
  snprintf(current,sizeof(current),"%s",relative);
  char *slash=strrchr(current,'/');if(slash)slash[1]=0;else current[0]=0;
  int result=lua_pcall(L,0,LUA_MULTRET,0);
  snprintf(current,sizeof(current),"%s",previous);
  if(result!=LUA_OK)return lua_error(L);
  return lua_gettop(L)-base;
}
static int add_client_file(lua_State *L) {
  // Local realms share mounted sources; record distribution requests for the host.
  const char *path=luaL_optstring(L,1,"");
  if(*path&&!path_ok(path))return luaL_error(L,"Invalid AddCSLuaFile path");
  lua_getglobal(L,"_GAMEBOX_CLIENT_FILES");
  lua_pushboolean(L,1);lua_setfield(L,-2,path);return 0;
}
static int module_compat(lua_State *L) {
  const char *name=luaL_checkstring(L,1);
  int options=lua_gettop(L);
  lua_getglobal(L,name);
  if(!lua_istable(L,-1)) {lua_pop(L,1);lua_newtable(L);lua_pushvalue(L,-1);lua_setglobal(L,name);}
  lua_pushvalue(L,-1);lua_setfield(L,-2,"_M");
  lua_pushstring(L,name);lua_setfield(L,-2,"_NAME");
  lua_pushliteral(L,"");lua_setfield(L,-2,"_PACKAGE");
  lua_Debug ar;
  if(!lua_getstack(L,1,&ar)||!lua_getinfo(L,"f",&ar))return luaL_error(L,"module caller unavailable");
  int fn=lua_gettop(L);
  for(int i=1;;i++) {
    const char *up=lua_getupvalue(L,fn,i);
    if(!up)break;
    lua_pop(L,1);
    if(strcmp(up,"_ENV")==0) {
      lua_pushvalue(L,fn-1);lua_setupvalue(L,fn,i);
      for(int option=2;option<=options;option++)if(lua_isfunction(L,option)){
        lua_pushvalue(L,option);lua_pushvalue(L,fn-1);lua_call(L,1,0);
      }
      return 0;
    }
  }
  return luaL_error(L,"module caller has no environment");
}
static int curtime(lua_State *L) {lua_pushnumber(L,game_time);return 1;}
static void global_fn(const char *name,lua_CFunction fn) {lua_pushcfunction(vm,fn);lua_setglobal(vm,name);}
void gb_close(void) {if(vm)lua_close(vm);vm=NULL;}
int gb_open(int server,const char *lua_root) {
  gb_close();allocated=0;current[0]=0;error_text[0]=0;game_time=0;
  if(strlen(lua_root)>=sizeof(root))return 0;
  snprintf(root,sizeof(root),"%s",lua_root);
  vm=lua_newstate(limited_alloc,NULL);if(!vm)return 0;
  const struct {const char *name;lua_CFunction fn;} libs[]={
    {"_G",luaopen_base},{"table",luaopen_table},{"string",luaopen_string},
    {"math",luaopen_math},{"utf8",luaopen_utf8}};
  for(size_t i=0;i<sizeof(libs)/sizeof(libs[0]);i++){luaL_requiref(vm,libs[i].name,libs[i].fn,1);lua_pop(vm,1);}
  lua_pushnil(vm);lua_setglobal(vm,"dofile");lua_pushnil(vm);lua_setglobal(vm,"loadfile");
  // Reject foreign/native bytecode even when load() is used by an addon.
  lua_pushboolean(vm,server);lua_setglobal(vm,"SERVER");
  lua_pushboolean(vm,!server);lua_setglobal(vm,"CLIENT");
  lua_pushboolean(vm,0);lua_setglobal(vm,"MENU_DLL");
  lua_newtable(vm);lua_setglobal(vm,"_GAMEBOX_CLIENT_FILES");
  global_fn("include",include_file);global_fn("AddCSLuaFile",add_client_file);
  global_fn("module",module_compat);global_fn("CurTime",curtime);
  global_fn("RealTime",curtime);
  lua_sethook(vm,budget,LUA_MASKCOUNT,1000);
  return 1;
}
const char *gb_error(void) {return error_text;}
static int finish(int status,int base) {
  if(status==LUA_OK)status=lua_pcall(vm,0,0,0);
  if(status!=LUA_OK){
    const char *message=lua_tostring(vm,-1);
    luaL_traceback(vm,vm,message?message:"Lua error (non-string)",1);
    snprintf(error_text,sizeof(error_text),"%s",lua_tostring(vm,-1));
  }else error_text[0]=0;
  lua_settop(vm,base);current[0]=0;
  return status==LUA_OK;
}
int gb_run(const char *source,const char *name) {
  if(!vm)return 0;
  int base=lua_gettop(vm);remaining=1000;
  return finish(luaL_loadbufferx(vm,source,strlen(source),name,"t"),base);
}
int gb_compile(const char *source,const char *name) {
  if(!vm)return 0;
  int base=lua_gettop(vm);
  int status=luaL_loadbufferx(vm,source,strlen(source),name,"t");
  if(status!=LUA_OK)snprintf(error_text,sizeof(error_text),"%s",lua_tostring(vm,-1));
  else error_text[0]=0;
  lua_settop(vm,base);return status==LUA_OK;
}
int gb_pcall(int arguments,int results) {
  if(!vm)return 0;
  int base=lua_gettop(vm)-arguments-1;remaining=1000;
  int status=lua_pcall(vm,arguments,results,0);
  if(status!=LUA_OK){
    const char *message=lua_tostring(vm,-1);
    snprintf(error_text,sizeof(error_text),"%s",message?message:"Lua callback error");
    lua_settop(vm,base);return 0;
  }
  return 1;
}
int gb_file(const char *path) {
  if(!vm)return 0;
  if(!path_ok(path)){snprintf(error_text,sizeof(error_text),"Invalid Lua path: %s",path);return 0;}
  int base=lua_gettop(vm);remaining=1000;
  lua_pushcfunction(vm,include_file);lua_pushstring(vm,path);
  int status=lua_pcall(vm,1,0,0);
  if(status!=LUA_OK){snprintf(error_text,sizeof(error_text),"%s",lua_tostring(vm,-1));lua_settop(vm,base);current[0]=0;return 0;}
  error_text[0]=0;return 1;
}
void gb_tick(double time) {game_time=time;}
#ifdef GB_NATIVE
int main(int argc,char **argv) {
  if(argc<3){fprintf(stderr,"Usage: gmod-lua LUA_ROOT FILE...\n");return 2;}
  if(!gb_open(1,argv[1]))return 2;
  int status=0;
  for(int i=2;i<argc;i++)if(!gb_file(argv[i])){fprintf(stderr,"%s\n",gb_error());status=1;}
  gb_close();return status;
}
#endif
