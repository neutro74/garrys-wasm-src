import makeLua from './build/gmod-lua.mjs';
import { addonPath } from './addons.mjs';

async function bootstrapText() {
  const url=new URL('./bootstrap.lua',import.meta.url);
  if(typeof process!=='undefined' && process.versions?.node) {
    const fs=await import('node:fs/promises');return fs.readFile(url,'utf8');
  }
  const response=await fetch(url);
  if(!response.ok)throw new Error(`GLua bootstrap HTTP ${response.status}`);
  return response.text();
}

export async function createRealm(realm, { files=new Map(), print=console.log, printErr=console.error }={}) {
  if(!['server','client'].includes(realm))throw new Error(`Unknown GLua realm: ${realm}`);
  const mod=await makeLua({print,printErr});
  const paths=new Set();let closed=false;
  function check() {if(closed)throw new Error('GLua realm is closed');}
  function call(name,types,args) {
    check();
    // Emscripten's ccall string arguments use the C stack. Workshop Lua files
    // can be hundreds of KiB, so put all source arguments on the heap instead.
    const pointers=[];
    try {
      const params=args.map((arg,i)=>{
        if(types[i]!=='string')return arg;
        const bytes=new TextEncoder().encode(arg),ptr=mod._malloc(bytes.length+1);
        if(!ptr)throw new Error('GLua argument allocation failed');
        pointers.push(ptr);mod.stringToUTF8(arg,ptr,bytes.length+1);return ptr;
      });
      if(!mod.ccall(name,'number',types.map(t=>t==='string'?'number':t),params))
        throw new Error(mod.ccall('gb_error','string',[],[])||`GLua ${name} failed`);
    }finally{for(const ptr of pointers)mod._free(ptr);}
  }
  function mount(path,data) {
    check();path=addonPath(path);
    // Assets remain in the host mount for later Source integration, not the VM heap.
    if(!path.startsWith('lua/'))return;
    const target='/game/'+path;
    mod.FS.mkdirTree(target.slice(0,target.lastIndexOf('/')));
    mod.FS.writeFile(target,typeof data==='string'?new TextEncoder().encode(data):data);
    paths.add(path.slice(4));
  }
  for(const [p,data] of files)mount(p,data);
  mod.FS.mkdirTree('/game/lua');
  call('gb_open',['number','string'],[realm==='server'?1:0,'/game/lua']);
  const api={
    realm,mount,
    run(source,name='=GameBox') {call('gb_run',['string','string'],[source,name]);},
    compile(source,name='=GameBox') {call('gb_compile',['string','string'],[source,name]);},
    include(path) {call('gb_file',['string'],[addonPath(path)]);},
    setTime(time) {check();if(!Number.isFinite(time)||time<0)throw new Error('Invalid game time');mod.ccall('gb_tick',null,['number'],[time]);},
    autorun() {
      check();
      const common=[...paths].filter(p=>/^autorun\/[^/]+\.lua$/.test(p)).sort();
      const specific=[...paths].filter(p=>p.startsWith(`autorun/${realm}/`) && p.split('/').length===3 && p.endsWith('.lua')).sort();
      const loaded=[],failed=[];
      for(const path of [...common,...specific]) {
        try {api.include(path);loaded.push(path);}catch(error){failed.push({path,error:error.message});}
      }
      return {loaded,failed};
    },
    close() {if(!closed){mod.ccall('gb_close',null,[],[]);closed=true;}},
  };
  try {api.run(await bootstrapText(),'@gamebox/bootstrap.lua');return api;}
  catch(error){api.close();throw error;}
}
