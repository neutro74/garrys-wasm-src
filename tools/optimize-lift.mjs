// Optimize immutable lifted artifacts in a new staging directory; never changes the source.
import fs from 'node:fs';import {spawnSync} from 'node:child_process';import path from 'node:path';
const arg=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i<0?fallback:process.argv[i+1];};
const inlineSize=+arg('inline-size',64),inlineBudget=+arg('inline-budget',16384);
if(!Number.isSafeInteger(inlineSize)||inlineSize<1||inlineSize>4096||!Number.isSafeInteger(inlineBudget)||inlineBudget<1||inlineBudget>262144)throw Error('Invalid inlining limits');
let failures=0;
const source=path.resolve(arg('source','tools/lift/out')),out=path.resolve(arg('out','run/out-n4000-o3-new'));
if(fs.existsSync(out))throw Error('Output exists; choose a fresh staging directory');
fs.mkdirSync(out,{recursive:true});
const files=new Set(),wasm=[];
for(const name of fs.readdirSync(source).filter(n=>n.endsWith('-thread.json'))){const m=JSON.parse(fs.readFileSync(`${source}/${name}`));for(const f of [name,name.replace('.json','.img'),m.core,...m.chunks])files.add(f);if(/^(client|engine|matsys|shaderdx9|lua|tier0)-/.test(name))wasm.push(m.core,...m.chunks);}
for(const f of files)fs.copyFileSync(`${source}/${f}`,`${out}/${f}`,fs.constants.COPYFILE_FICLONE);
// The Lua VM static-branch build has separately passed its real Lua checks.
const lua=arg('lua',null);
if(lua){const lm=JSON.parse(fs.readFileSync(lua+'.json')),dir=path.dirname(lua),prefix=path.basename(lua);
for(const f of [prefix+'.json',prefix+'.img',lm.core,...lm.chunks])fs.copyFileSync(path.join(dir,f),path.join(out,f),fs.constants.COPYFILE_FICLONE);}
for(const f of wasm){
 const before=fs.statSync(`${out}/${f}`).size;const tmp=`${out}/${f}.opt`;
 const r=spawnSync('systemd-run',['--user','--scope','--quiet','-p','MemoryMax=2200M','-p','MemorySwapMax=0','--','env','BINARYEN_CORES=2','taskset','-c','4-15','third_party/binaryen/wasm-opt',`${out}/${f}`,'--enable-tail-call','--enable-simd','--enable-bulk-memory','--enable-mutable-globals','--enable-sign-ext','--enable-nontrapping-float-to-int','--enable-threads','-O3',`--always-inline-max-function-size=${inlineSize}`,`--inline-max-combined-binary-size=${inlineBudget}`,'--inlining-optimizing','-o',tmp],{stdio:'inherit'});
 if(r.status!==0){failures++;console.log('FAILED',f,r.status);continue;}
 const after=fs.statSync(tmp).size;
 // Bound binary size growth on the 4 GB target. Keep the original if aggressive inlining expands it too far.
 if(after>before*1.15){fs.unlinkSync(tmp);console.log('KEPT',f,'size growth',after/before);continue;}
 fs.renameSync(tmp,`${out}/${f}`);console.log('DONE',f,before,after);
}
if(failures){process.exitCode=1;console.error('FAILED_MODULES',failures);}else console.log('ALL_DONE');
