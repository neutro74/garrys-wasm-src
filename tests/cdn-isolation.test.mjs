import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source = () => readFile(new URL('../web/cdn-isolation.js',import.meta.url),'utf8');
async function run({isolated=false, supported=true, guarded=false, storageFailure=false, registerFailure=false}={}) {
 const values=new Map(guarded?[['garrys-wasm.cdn-isolation:/release/','1']]:[]);const registrations=[];const reloads=[];
 const context=vm.createContext({URL,console,globalThis:undefined,crossOriginIsolated:isolated,navigator:supported?{serviceWorker:{register:async(...args)=>{registrations.push(args);if(registerFailure)throw Error('blocked');return {};},ready:Promise.resolve()}}:{},location:{href:'https://example.test/release/web/index.html',replace:url=>reloads.push(url)},sessionStorage:{getItem:key=>{if(storageFailure)throw Error('blocked');return values.get(key);},setItem:(key,v)=>{if(storageFailure)throw Error('blocked');values.set(key,v);},removeItem:key=>values.delete(key)}});
 vm.runInContext('globalThis = this',context);
 vm.runInContext((await source()).replace('export async function','async function').replaceAll('import.meta.url',JSON.stringify('https://example.test/release/web/cdn-isolation.js')),context);
 const promise=vm.runInContext('ensureCdnIsolation()',context);
 await new Promise(resolve=>setImmediate(resolve));
 return {promise,context,registrations,reloads,values};
}
test('already isolated starts immediately; unsupported and failed registration fail without reload',async()=>{const isolated=await run({isolated:true});assert.equal(await isolated.promise,true);assert.equal(isolated.registrations.length,0);for(const options of [{supported:false},{registerFailure:true}]){const f=await run(options);assert.equal(await f.promise,false);assert.equal(f.reloads.length,0);}});
test('worker registers package-root scope and reloads only once',async()=>{const f=await run();assert.equal(f.registrations.length,1);assert.equal(f.registrations[0][0],'https://example.test/release/cdn-sw.js');assert.equal(f.registrations[0][1].scope,'https://example.test/release/');assert.equal(f.reloads.length,1);assert.match(f.reloads[0],/__gmod_cdn_isolation=1/);const guarded=await run({guarded:true});assert.equal(await guarded.promise,false);assert.equal(guarded.reloads.length,0);});
test('URL reload guard works even with session storage unavailable',async()=>{const f=await run({storageFailure:true});assert.equal(f.reloads.length,1);vm.runInContext(`location.href=${JSON.stringify(f.reloads[0])}`,f.context);assert.equal(await vm.runInContext('ensureCdnIsolation()',f.context),false);assert.equal(f.reloads.length,1);});
